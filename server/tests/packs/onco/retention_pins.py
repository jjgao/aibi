"""The marker scan's own pins (D438), run by ``test_retention`` in a child of their own: a heap
without the pack's, which a walk reads in a fraction of the time the suite's takes.

- **The reads, as a class** (``CELLS``): a holder of each kind the walk reads, holding each form
  of a marker in each encoding it can hold one in (the characters of a text, the bytes of UTF-8,
  UTF-16 and UTF-32 of either byte order, the code points as numbers), and the transforms the walk
  undoes (another case, reversed, cut into chunks, the hex and base64 of UTF-8, an ``int``'s
  bytes), most of them amid random characters, non-zero bytes or code points (never what the
  needles assume around a form: base64 at each alignment after non-zero bytes), and an ``int``'s
  bare form too (whose leading zero bytes a number drops). One marker for each encoding (its
  three forms in each holder), and each holder in a class of its own, which the hit's chain
  names: a cell is found when a hit of the cell's own read (``kind``) names its class. ``NAMED``
  says which mutant of D438's table each cell stands for. The pins' forms and encodings are their
  own, never the scan's ``forms()`` or ``ENCODINGS``, which a mutant changes.
- **The routes** (``ROUTES``): each way an object holds a marker (a dict's key or value, a default,
  a closure, an attribute, a module in ``sys.modules``...), one marker each; the walk without a
  dict's keys misses exactly the ones held only as a key, and once everything is released, no
  walk finds anything.
- **The order** (``ORDER``): a call that keeps a marker and the next call that drops it (NB04d's
  shape), through ``drive``, the scan's own loop: a walk after each call finds it after the
  first, and one walk after the last finds nothing.
- **The walk's totality** (``twins``): the matrix again, every holder and every part of it a
  generated twin (``TWINS``: a subclass of each type the walk reads that overrides every callable
  or data descriptor of its base, under a metaclass that does the same for the class), each slot
  recording that it ran and raising while armed: the walk finds every cell and nothing ran. And
  ``HOSTILE``: objects that raise on what a walk could reach besides (a metaclass's attribute
  lookup, a module named by a ``str`` subclass, a key that collides with ``__name__``), and
  holders whose read raises ``ValueError`` (``UNREADABLE``): each found, each chain plain ``str``.

It prints one JSON line: what was found of each, by name.
"""

import abc
import enum
import importlib.util
import inspect
import io
import json
import secrets
import sys
import types
import warnings
import weakref
from array import array, typecodes
from base64 import b64encode
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

SCAN = Path(__file__).resolve().with_name("retention_scan.py")
KEPT: list[object] = []
"""Where the pins hold what they hide: a heap object, which a frame's locals are not."""
ENCODINGS = ("text", "utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be", "code points")
"""The pins' own list, never the scan's, which a mutant changes."""


def _scan() -> Any:
    """``retention_scan.py``, loaded by its path (when the pins run, never when this is imported:
    no module here loads the pack, or what loads it, at import)."""
    spec = importlib.util.spec_from_file_location("retention_scan", SCAN)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Str(str):
    __slots__ = ()


class _Bytes(bytes):
    __slots__ = ()


class _Int(int):
    __slots__ = ()


class Cell(NamedTuple):
    """A holder of the matrix: its name, what it holds (``text``, ``bytes`` or ``points``), the
    read that must find it, the encodings it is made in (all of its unit's when empty), and what
    makes it from the form so encoded."""

    name: str
    unit: str
    kind: str
    make: Callable[[Any], object]
    only: tuple[str, ...] = ()


def _nonzero(size: int) -> bytes:
    """``size`` random bytes, none of them zero: what surrounds a form in a cell is never what the
    walk's needles assume (a needle built with zero bytes around a form must not match here)."""
    return bytes(secrets.choice(range(1, 256)) for _ in range(size))


def _around(b: bytes) -> bytes:
    """``b`` between random non-zero bytes."""
    return _nonzero(2) + b + _nonzero(3)


def _around_text(t: str) -> str:
    """``t`` between random characters (digits and punctuation: never a letter of a form)."""
    return "".join(secrets.choice("0123456789.,;:-+=") for _ in range(2)) + t + "#!"


def _around_points(p: list[int]) -> list[int]:
    """``p`` between random code points that fit every array type code."""
    return [secrets.choice(range(1, 128)) for _ in range(2)] + p + [secrets.choice(range(1, 128))]


def _base64(at: int, text: bool = False) -> Callable[[bytes], object]:
    """The base64 of a buffer that holds the bytes ``at`` (0, 1 or 2) random non-zero bytes in,
    as bytes or as text."""

    def make(b: bytes) -> object:
        encoded = b64encode(_nonzero(at) + b + _nonzero(4))
        return encoded.decode() if text else encoded

    return make


def _array(code: str) -> Callable[[Any], object]:
    def make(value: Any) -> object:
        with warnings.catch_warnings():  # array('u') is deprecated from 3.13
            warnings.simplefilter("ignore", DeprecationWarning)
            if code in "uw":
                return array(code, _around_text(value))
            points = _around_points(value)
            return array(code, [float(p) for p in points] if code in "fd" else points)

    return make


CELLS = (
    Cell("a str", "text", "str", lambda t: _around_text(t)),
    Cell("a str subclass", "text", "str", lambda t: _Str(_around_text(t))),
    Cell("a title-cased str", "text", "str", lambda t: _around_text(t).title()),
    Cell("a reversed str", "text", "str", lambda t: _around_text(t)[::-1]),
    Cell("a slice", "text", "str", lambda t: slice(_around_text(t), None)),
    Cell("an io.StringIO", "text", "StringIO", lambda t: io.StringIO(_around_text(t))),
    *(Cell(f"an array('{c}')", "text", f"array {c}", _array(c)) for c in typecodes if c in "uw"),
    Cell("a list of its characters", "text", "list items", lambda t: list(_around_text(t))),
    Cell("a list of str subclasses", "text", "list items", lambda t: [_Str(c) for c in t]),
    Cell("a tuple of its chunks", "text", "tuple items", lambda t: ("<" + t[:5], t[5:] + ">")),
    Cell("a deque of its characters", "text", "deque items", lambda t: deque(_around_text(t))),
    Cell("a dict's keys, its chunks", "text", "dict keys", lambda t: {t[:5]: 0, t[5:]: 1}),
    Cell("a dict's values, its characters", "text", "dict values", lambda t: dict(enumerate(t))),
    Cell("bytes", "bytes", "bytes", lambda b: _around(b)),
    Cell("a bytes subclass", "bytes", "bytes", lambda b: _Bytes(_around(b))),
    Cell("a bytearray", "bytes", "bytearray", lambda b: bytearray(_around(b))),
    Cell("a memoryview", "bytes", "memoryview", lambda b: memoryview(bytearray(_around(b)))),
    Cell("an io.BytesIO", "bytes", "BytesIO", lambda b: io.BytesIO(_around(b))),
    Cell("an array('B')", "bytes", "array B", lambda b: array("B", _around(b))),
    Cell("reversed bytes", "bytes", "bytes", lambda b: _around(b)[::-1]),
    Cell("an int, big-endian", "bytes", "int", lambda b: int.from_bytes(b, "big")),
    Cell("an int, little-endian", "bytes", "int", lambda b: int.from_bytes(b, "little")),
    Cell("an int amid bytes, big-endian", "bytes", "int", lambda b: int.from_bytes(_around(b))),
    Cell(
        "an int amid bytes, little-endian",
        "bytes",
        "int",
        lambda b: int.from_bytes(_around(b), "little"),
    ),
    Cell("an int subclass", "bytes", "int", lambda b: _Int(int.from_bytes(b, "big"))),
    Cell("a negative int", "bytes", "int", lambda b: -int.from_bytes(_around(b), "big")),
    Cell("a range", "bytes", "int", lambda b: range(0, int.from_bytes(_around(b), "big"))),
    Cell("its hex in a str", "bytes", "str", lambda b: _around(b).hex(), ("utf-8",)),
    Cell("its hex in capitals", "bytes", "str", lambda b: _around(b).hex().upper(), ("utf-8",)),
    Cell("its hex in bytes", "bytes", "bytes", lambda b: _around(b).hex().encode(), ("utf-8",)),
    *(
        Cell(f"its base64 in bytes, {at} bytes in", "bytes", "bytes", _base64(at), ("utf-8",))
        for at in range(3)
    ),
    *(
        Cell(f"its base64 in a str, {at} bytes in", "bytes", "str", _base64(at, True), ("utf-8",))
        for at in range(3)
    ),
    *(
        Cell(f"an array('{c}') of its code points", "points", f"array {c}", _array(c))
        for c in typecodes
        if c not in "uw"
    ),
    Cell("a list of its code points", "points", "list items", lambda p: _around_points(p)),
    Cell("a list of int subclasses", "points", "list items", lambda p: [_Int(c) for c in p]),
    Cell("a tuple of its code points", "points", "tuple items", lambda p: tuple(_around_points(p))),
    Cell("a deque of its code points", "points", "deque items", lambda p: deque(_around_points(p))),
    Cell("a dict's values, code points", "points", "dict values", lambda p: dict(enumerate(p))),
)
"""Every holder of a form's text, bytes or code points the walk reads, most amid random
characters, non-zero bytes or code points (an ``int``'s bare form too, whose leading zero bytes a
number drops)."""
NAMED: Mapping[str, tuple[str, str]] = {
    "NB03": ("an io.StringIO", "text"),
    "NB05": ("a title-cased str", "text"),
    "NB06": ("an array('u')", "text"),
    "NB16": ("a slice", "text"),
    "C11": ("an array('H') of its code points", "code points"),
    "C11i": ("an array('i') of its code points", "code points"),
    "C12": ("an array('u')", "text"),
    "C13": ("bytes", "utf-16-le"),
    "C15": ("a slice", "text"),
    "C16": ("a title-cased str", "text"),
    "C17": ("an io.StringIO", "text"),
    "C18": ("a reversed str", "text"),
    "C40": ("a list of its code points", "code points"),
    "C41": ("a deque of its code points", "code points"),
    "C42": ("bytes", "utf-32-le"),
    "C43": ("an array('d') of its code points", "code points"),
    "C44": ("a list of its characters", "text"),
    "C45": ("a tuple of its chunks", "text"),
    "C46": ("an int, big-endian", "utf-8"),
    "C46b": ("an int, little-endian", "utf-8"),
    "C47": ("its hex in a str", "utf-8"),
    "C48": ("its base64 in bytes, 0 bytes in", "utf-8"),
    "C49": ("an io.BytesIO", "utf-8"),
    "C50": ("a memoryview", "utf-8"),
}
"""The mutants of D438's table (``retention_mutants/table.py``) that a cell of the matrix stands
for: each is a pack keeping a marker in that holder, encoded so."""


def _encoded(scan: Any, form: str, encoding: str) -> Any:
    if encoding == "text":
        return scan.fresh(form)
    if encoding == "code points":
        return [ord(character) for character in form]
    return form.encode(encoding)


def matrix(scan: Any, convert: Callable[[object], object] = lambda held: held) -> dict[str, Any]:
    """Every cell, for every encoding of its unit and every form, made, given to ``convert`` and
    held in a class of its own; one walk (armed, when ``convert`` makes twins); the cells it found
    by their own read, and the ones it did not."""
    markers = [scan.Marker.new() for _ in ENCODINGS]
    labels: dict[str, tuple[int, str]] = {}
    for index, (encoding, marker) in enumerate(zip(ENCODINGS, markers, strict=True)):
        unit = {"text": "text", "code points": "points"}.get(encoding, "bytes")
        for cell in CELLS:
            if cell.unit != unit or (cell.only and encoding not in cell.only):
                continue
            for form_name in ("lower", "upper", "marks"):  # never forms(), which a mutant changes
                form = getattr(marker, form_name)
                name = f"cell{len(labels)}"
                held = convert(cell.make(_encoded(scan, form, encoding)))
                KEPT.append(type(name, (), {"held": held}))
                labels[f"type {name}"] = (index, f"{cell.name} / {encoding} / {form_name}")
    kinds = {f"{cell.name} / {e}": cell.kind for cell in CELLS for e in ENCODINGS}
    found: set[str] = set()
    ARMED.append(True)
    try:
        armed = ARMED == [True]  # the walk's, as a twin's slot would read it
        hits = scan.reachable(markers)
    finally:
        ARMED.clear()
    for hit in hits:
        for step in hit.chain:
            if step in labels:
                index, label = labels[step]
                if index == hit.call and kinds[label.rsplit(" / ", 1)[0]] == hit.kind:
                    found.add(label)
    every = sorted(label for _, label in labels.values())
    KEPT.clear()
    named = {
        name: sorted(label for label in every if label.startswith(f"{cell} / {encoding} / "))
        for name, (cell, encoding) in NAMED.items()
    }
    return {"cells": every, "missing": sorted(set(every) - found), "named": named, "armed": armed}


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


def _make_default_dict_key(text: str) -> object:
    held: defaultdict[str, int] = defaultdict(int)
    held[text + "k"] = 1
    return held


def _make_module(text: str) -> object:
    module = types.ModuleType("scratch_held")
    module.__dict__[text + "n"] = 1
    sys.modules["scratch_held"] = module
    return module


ROUTES: Mapping[str, tuple[Callable[[str], object], str]] = {
    "a dict's key": (_make_dict_key, "lower"),
    "a dict's value": (_make_dict_value, "lower"),
    "a list in a list": (_make_list, "lower"),
    "a set of tuples": (_make_set, "lower"),
    "a default argument": (_make_default, "lower"),
    "a keyword default": (_make_kwdefault, "lower"),
    "a closure cell": (_make_closure, "lower"),
    "an instance's attribute": (_make_attribute, "lower"),
    "a class attribute": (_make_class_attribute, "lower"),
    "a defaultdict's key": (_make_default_dict_key, "lower"),
    "a module's name": (_make_module, "lower"),
    "the upper case form": (_make_list, "upper"),
    "the marks form": (_make_set, "marks"),
}
"""Each way an object holds a marker, with the form of the marker it holds."""


def _method(self: object) -> object:
    return self


def _untracked(text: str) -> tuple[str, int]:
    """A tuple of atoms, which the collector stops tracking: reached through its holder alone."""
    return (text, 1)


FOLLOWED: Mapping[type, Callable[[str], object]] = {
    type: lambda t: type(t, (), {}),
    types.FunctionType: lambda t: (lambda held: lambda x=held: x)(_untracked(t)),
    types.CodeType: lambda t: compile(f"x = ({t!r}, 1)", "<held>", "exec"),
    types.CellType: lambda t: types.CellType(_untracked(t)),
    types.MethodType: lambda t: types.MethodType(_method, _untracked(t)),
    types.BuiltinFunctionType: lambda t: {"k": _untracked(t)}.get,
    types.MethodWrapperType: lambda t: _untracked(t).__add__,
    types.MappingProxyType: lambda t: types.MappingProxyType({"k": _untracked(t)}),
    property: lambda t: property(None, None, None, _untracked(t)),  # type: ignore[arg-type]
    staticmethod: lambda t: staticmethod(_untracked(t)),  # type: ignore[arg-type]
    classmethod: lambda t: classmethod(_untracked(t)),  # type: ignore[arg-type]
    set: lambda t: {_untracked(t)},
    frozenset: lambda t: frozenset({_untracked(t)}),
    tuple: lambda t: (_untracked(t),),
    list: lambda t: [_untracked(t)],
    dict: lambda t: {"k": _untracked(t)},
}
"""For each type the walk follows (the scan's ``PLAIN`` and its three sequences), a route that
holds a marker behind an instance of it alone: the marker in an untracked tuple of atoms, or in
a class's name, or in a code object's constants, which nothing else reaches. Made a leaf, or not
followed, the type loses its route."""


def _one(a: object) -> object:
    return a


def _outer() -> Callable[[], int]:
    held = 1

    def inner() -> int:
        return held

    return inner


def _named(name: str | None = None, qualname: str | None = None) -> type:
    """A class made by ``type`` with only its name, or only its qualified name, set."""
    made = type("Held", (), {})
    if name is not None:
        made.__name__ = name
    if qualname is not None:
        made.__qualname__ = qualname
    return made


PARTS: Mapping[str, Callable[[str], object]] = {
    "co_consts": lambda t: compile(f"x = {t!r}", "<held>", "exec"),
    "co_names": lambda t: compile(f"{t}.x", "<held>", "eval"),
    "co_varnames": lambda t: _one.__code__.replace(co_varnames=(t,)),
    "co_freevars": lambda t: _outer().__code__.replace(co_freevars=(t,)),
    "co_cellvars": lambda t: _outer.__code__.replace(co_cellvars=(t,)),
    "co_filename": lambda t: compile("x = 1", t, "exec"),
    "co_name": lambda t: compile("x = 1", "<held>", "exec").replace(co_name=t),
    "co_qualname": lambda t: compile("x = 1", "<held>", "exec").replace(co_qualname=t),
}
"""For each part of a code object the walk reads (``CODE_READ``), a code object that holds a
marker in that part alone (a code object has no traverse: nothing else reaches it)."""
NAMES: Mapping[str, Callable[[str], object]] = {
    "a code object nested in another's constants": lambda t: compile(
        f"def f():\n    return {t!r}\n", "<held>", "exec"
    ),
    "a class named alone": lambda t: _named(name=t),
    "a class qualified alone": lambda t: _named(qualname=t),
    "a class an abc.ABCMeta made": lambda t: abc.ABCMeta(t, (), {}),
    "a class an enum.Enum made": lambda t: enum.Enum(t, ["A"]),
}
"""The rest of what the walk reads besides a type's referents: a code object in another, a
class's name and its qualified name, each alone, and a class a metaclass made, by its names."""
UNFOLLOWED: Mapping[type, str] = {
    types.WrapperDescriptorType: "a descriptor holds its class, a root of its own",
    types.MethodDescriptorType: "a descriptor holds its class, a root of its own",
    types.GetSetDescriptorType: "a descriptor holds its class, a root of its own",
    types.MemberDescriptorType: "a descriptor holds its class, a root of its own",
    weakref.ReferenceType: "a weak reference holds its callback, which is tracked: a root",
}
"""The types the walk follows that hold nothing a root does not: no route can be theirs alone."""


def routes(scan: Any) -> dict[str, Any]:
    """Each route, a marker of its own: found by one walk (with the chain of a default naming its
    function), and by the walk without a dict's keys; and found by none once released. Each
    type the walk follows has a route of ``FOLLOWED`` or is named in ``UNFOLLOWED``."""
    names = list(ROUTES)
    markers = [scan.Marker.new() for _ in names]
    for marker, (make, form) in zip(markers, ROUTES.values(), strict=True):
        KEPT.append(make(scan.fresh(getattr(marker, form))))
    more = {f"behind a {kind.__name__}": make for kind, make in FOLLOWED.items()}
    more |= {f"in a code object's {part} alone": make for part, make in PARTS.items()}
    for name, make in {**more, **NAMES}.items():
        names.append(name)
        markers.append(scan.Marker.new())
        KEPT.append(make(scan.fresh(markers[-1].lower)))
    hits = scan.reachable(markers)
    default = names.index("a default argument")
    chained = any(
        "_make_default" in step for hit in hits if hit.call == default for step in hit.chain
    )
    found = sorted({names[hit.call] for hit in hits})
    del hits  # a chain names a class, and a route names a class by its marker
    without = {names[hit.call] for hit in scan.reachable(markers, keys=False)}
    KEPT.clear()
    sys.modules.pop("scratch_held", None)
    return {
        "found": found,
        "routes": sorted(names),
        "default chained": chained,
        "missed without keys": sorted(set(names) - without),
        "after release": len(scan.reachable(markers)),
        "code parts without a route": sorted(set(scan.CODE_READ) ^ set(PARTS)),
        "followed without a route": len(
            (set(scan.PLAIN) | {id(tuple), id(list), id(dict)})
            - {id(kind) for kind in (*FOLLOWED, *UNFOLLOWED)}
        ),
    }


ORDER: list[str] = []
"""What the order's first call keeps and its second drops."""


def order(scan: Any) -> dict[str, Any]:
    """NB04d's shape through ``drive``: kept by the first call, dropped by the second."""
    markers = [scan.Marker.new() for _ in range(3)]

    def keep() -> str:
        ORDER.append(scan.fresh(markers[0].lower))
        return "ok"

    def drop() -> str:
        ORDER.clear()
        return "ok"

    calls = [keep, drop, lambda: "ok"]
    each: list[int] = []

    def after(done: int) -> None:
        if scan.reachable(markers[:done]):
            each.append(done)

    outcomes = scan.drive(calls, after)
    scan.drive(calls)
    return {"outcomes": outcomes, "walk after each": each, "one walk": len(scan.reachable(markers))}


# --- Objects that run code on any read but the base type's own -----------------------------


class _Hostile(type):
    """A metaclass whose hash, comparison and attribute lookup raise once armed: a walk that puts
    one of its classes in a set, compares it or asks it for its name runs the pack's code."""

    def __hash__(cls) -> int:
        raise RuntimeError("the walk hashed a class")

    def __eq__(cls, other: object) -> bool:
        raise RuntimeError("the walk compared a class")

    def __getattribute__(cls, name: str) -> object:
        if ARMED:
            raise RuntimeError("the walk asked a class for an attribute")
        return super().__getattribute__(name)


ARMED: list[bool] = []
"""Non-empty while the hostile objects are armed (a list, so that it is read, not rebound)."""


def _refusal(label: str) -> Any:
    """A function that raises, naming the slot of the class that was called: a walk that regresses
    fails with the read that ran code."""

    def refuse(*_: object, **__: object) -> Any:
        raise RuntimeError(f"the walk called {label}")

    return refuse


def _refusing(*slots: str) -> Callable[[type], type]:
    """A class decorator that gives a class the slots, each raising, naming itself."""

    def decorate(cls: type) -> type:
        for slot in slots:
            setattr(cls, slot, _refusal(f"{cls.__name__}.{slot}"))
        return cls

    return decorate


SEQUENCE_SLOTS = ("__eq__", "__hash__", "__len__", "__iter__", "__getitem__", "__contains__")


@_refusing("__eq__", "__hash__", "__len__")
class _HostileInstance(metaclass=_Hostile):
    """An instance whose attribute lookup, hash, comparison and length raise once armed."""

    def __getattribute__(self, name: str) -> object:
        if ARMED:
            raise RuntimeError("the walk asked an object for an attribute")
        return super().__getattribute__(name)


@_refusing(*SEQUENCE_SLOTS, "__format__", "casefold", "lower", "__str__")
class _HostileStr(str):
    pass


@_refusing(*SEQUENCE_SLOTS, "lower", "hex")
class _HostileBytes(bytes):
    def __buffer__(self, flags: int, /) -> memoryview:
        """A buffer the walk must never ask for: ``re`` and ``bytes()`` would (PEP 688)."""
        raise RuntimeError("the walk asked a bytes subclass for its buffer")


@_refusing(*SEQUENCE_SLOTS)
class _HostileBytearray(bytearray):
    def __buffer__(self, flags: int, /) -> memoryview:
        raise RuntimeError("the walk asked a bytearray subclass for its buffer")


@_refusing("tobytes", "tolist", "tounicode", "__contains__", "__len__", "__iter__", "__buffer__")
class _HostileArray(array):  # type: ignore[type-arg]
    typecode = property(_refusal("_HostileArray.typecode"))  # type: ignore[assignment]


@_refusing("__eq__", "__hash__", "__len__", "__iter__", "keys", "items", "values", "get")
class _HostileDict(dict):  # type: ignore[type-arg]
    pass


@_refusing(*SEQUENCE_SLOTS, "__reversed__")
class _HostileList(list):  # type: ignore[type-arg]
    pass


@_refusing(*SEQUENCE_SLOTS)
class _HostileTuple(tuple):  # type: ignore[type-arg]
    pass


@_refusing(*SEQUENCE_SLOTS, "__reversed__")
class _HostileDeque(deque):  # type: ignore[type-arg]
    pass


@_refusing("getvalue", "read", "readline", "__iter__", "__next__", "tell", "seek", "__getstate__")
class _HostileStringIO(io.StringIO):
    pass


@_refusing("getvalue", "getbuffer", "read", "__iter__", "__next__", "tell", "seek", "__getstate__")
class _HostileBytesIO(io.BytesIO):
    pass


@_refusing(
    "__index__", "__int__", "__eq__", "__hash__", "__format__", "__lt__", "__gt__", "__neg__"
)
class _HostileInt(int):
    pass


class _Unopened(io.StringIO):
    """A ``StringIO`` whose ``__init__`` never ran: its value cannot be read (``ValueError``)."""

    def __init__(self) -> None:
        pass


class _Colliding:
    """A key that hashes as ``__name__`` does and raises on a comparison once armed."""

    def __hash__(self) -> int:
        return hash("__name__")

    def __eq__(self, other: object) -> bool:
        if ARMED:
            raise RuntimeError("the walk compared a key")
        return self is other


def _hostile_instance(text: str) -> object:
    ARMED.clear()
    holder = _HostileInstance()
    holder.kept = [text]  # type: ignore[attr-defined]
    return holder


def _hostile_class(text: str) -> object:
    ARMED.clear()
    return _Hostile("Held", (), {"kept": (text,)})


def _hostile_module_name(text: str) -> object:
    """A module whose ``__name__`` is a ``str`` subclass that raises on a comparison."""
    module = types.ModuleType("scratch_hostile")
    module.__dict__["__name__"] = _HostileStr("scratch_hostile")
    module.__dict__["kept"] = [text]
    return module


def _hostile_module_key(text: str) -> object:
    """A module whose dict has, probed before ``__name__``, a key that collides with it."""
    module = types.ModuleType("scratch_hostile")
    module.__dict__.clear()
    module.__dict__[_Colliding()] = 1  # type: ignore[index]
    module.__dict__["__name__"] = "scratch_hostile"
    module.__dict__["kept"] = [text]
    return module


def _hostile_type_name(text: str) -> object:
    """An instance of a class named by a ``str`` subclass, whose format raises."""
    ARMED.clear()
    kind = type("Named", (), {})
    kind.__name__ = _HostileStr("Named")
    holder = kind()
    holder.kept = [text]  # type: ignore[attr-defined]
    return holder


def _hostile_qualname(text: str) -> object:
    """A class whose ``__qualname__`` is a ``str`` subclass: it is in the chain of the marker."""
    kind = type("Named", (), {"kept": (text,)})
    kind.__qualname__ = _HostileStr("Named")
    return kind


def _points(text: str) -> list[int]:
    return [ord(character) for character in text]


HOSTILE: Mapping[str, Callable[[str], object]] = {
    "an instance of a class whose metaclass runs code": _hostile_instance,
    "a class whose metaclass runs code": _hostile_class,
    "a str subclass": lambda t: [_HostileStr("xx" + t + "yy")],
    "a bytes subclass with a buffer": lambda t: [_HostileBytes(b"xx" + t.encode() + b"yy")],
    "a bytearray subclass with a buffer": lambda t: [_HostileBytearray(t.encode("utf-16-le"))],
    "an array subclass": lambda t: [_HostileArray("b", t.encode())],
    "an array subclass of code points": lambda t: [_HostileArray("H", _points(t))],
    "a dict subclass": lambda t: _HostileDict({1: [t]}),
    "a dict subclass's keys": lambda t: [_HostileDict({t[:5]: 0, t[5:]: 1})],
    "a list subclass of characters": lambda t: [_HostileList(t)],
    "a tuple subclass of chunks": lambda t: [_HostileTuple((t[:5], t[5:]))],
    "a deque subclass of code points": lambda t: [_HostileDeque(_points(t))],
    "a list of str subclasses": lambda t: [[_HostileStr(c) for c in t]],
    "a list of int subclasses": lambda t: [[_HostileInt(c) for c in _points(t)]],
    "an int subclass": lambda t: [_HostileInt(int.from_bytes(t.encode(), "big"))],
    "a StringIO subclass": lambda t: [_HostileStringIO(t)],
    "a BytesIO subclass": lambda t: [_HostileBytesIO(t.encode("utf-32-be"))],
    "a module named by a str subclass": _hostile_module_name,
    "a module dict with a key that collides with __name__": _hostile_module_key,
    "an instance of a class named by a str subclass": _hostile_type_name,
    "a class qualified by a str subclass": _hostile_qualname,
}
"""Objects of types that run code on a hash, a comparison, a length, an iteration, an attribute
lookup, an index, a buffer or a read, each holding a marker: the walk must finish, and find
each."""


def _closed(text: str) -> object:
    held = io.StringIO(text)
    held.close()
    return held


def _released(text: str) -> object:
    held = memoryview(bytearray(text.encode()))
    held.release()
    return held


UNREADABLE: Mapping[str, Callable[[str], object]] = {
    "a closed StringIO": _closed,
    "a StringIO never initialised": lambda _: _Unopened(),
    "a closed BytesIO": lambda t: (b := io.BytesIO(t.encode()), b.close())[0],
    "a released memoryview": _released,
}
"""Holders whose read raises (``ValueError``: closed, released, never initialised): the walk
catches it and goes on."""


def hostile(scan: Any) -> dict[str, Any]:
    """Every object of ``HOSTILE`` and ``UNREADABLE`` held, armed, and one walk."""
    names = list(HOSTILE)
    markers = [scan.Marker.new() for _ in names]
    for marker, make in zip(markers, HOSTILE.values(), strict=True):
        KEPT.append(make(scan.fresh(marker.lower)))
    KEPT.extend(make(scan.fresh(scan.Marker.new().lower)) for make in UNREADABLE.values())
    ARMED.append(True)
    try:
        found = scan.reachable(markers)
    finally:
        ARMED.clear()
        KEPT.clear()
    return {
        "held": sorted(names),
        "found": sorted({names[hit.call] for hit in found}),
        "plain chains": all(type(step) is str for hit in found for step in hit.chain),
    }


# --- Twins: a subclass of each type the walk reads, every slot of its base recording -----------

SPARED = frozenset(
    {"__new__", "__init__", "__init_subclass__", "__class_getitem__", "__subclasshook__"}
    | {"__class__", "__doc__"}
)
"""What a twin leaves to its base: what makes one, and what is not a slot."""
RAN: list[str] = []
"""Every slot of a twin, or of its class, that ran while the twins were armed."""


def _recording(owner: str, name: str, original: Callable[..., Any]) -> Callable[..., Any]:
    """A slot that, armed, records that it ran and raises (a swallowed raise is still seen), and
    is its base's otherwise."""

    def slot(*args: Any, **kwargs: Any) -> Any:
        if ARMED:
            RAN.append(f"{owner}.{name}")
            raise RuntimeError(f"the walk ran {owner}.{name}")
        return original(*args, **kwargs)

    return slot


def _slots(base: type, spared: frozenset[str]) -> dict[str, Any]:
    """Every attribute of ``base`` that is callable or a data descriptor (``spared`` aside), each
    as a slot that records and raises when armed and is ``base``'s otherwise (a data descriptor
    as a property of its getter)."""
    found: dict[str, Any] = {}
    for name in dir(base):
        if name in spared:
            continue
        attribute = inspect.getattr_static(base, name)
        if isinstance(attribute, types.GetSetDescriptorType | types.MemberDescriptorType):
            found[name] = property(_recording(base.__name__, name, attribute.__get__))
        elif callable(getattr(base, name)):
            found[name] = _recording(base.__name__, name, getattr(base, name))
    return found


META_SPARED = SPARED | {"__prepare__", "__call__", "mro", "__dict__", "__name__", "__qualname__"}
"""What the twins' metaclass leaves to ``type``: what makes a class, and the parts of a class the
interpreter reads as attributes while it makes or calls one (its ``__dict__``, its names, its
method resolution order, its call); the walk reads a class's names through ``type``'s own
descriptors, never through the class's metaclass."""
_Recorded: type = type("_Recorded", (type,), _slots(type, META_SPARED))
"""The twins' metaclass: every other slot of ``type`` (a hash, a comparison, an attribute lookup,
a format, a subclass or instance check, a size...) records and raises, armed."""


def _twin(base: type) -> type:
    """A subclass of ``base`` that overrides every attribute of ``base`` that is callable or a
    data descriptor (``SPARED`` aside), under the twins' metaclass."""
    return _Recorded(f"Twin{base.__name__}", (base,), _slots(base, SPARED))


BASES: tuple[type, ...] = (str, bytes, bytearray, int, array, list, tuple, deque, dict)
TWINS: dict[type, type] = {
    base: _twin(base) for base in (*BASES, io.StringIO, io.BytesIO, types.ModuleType)
}
"""A twin of each type the walk reads through its base type's own methods, and of a module, which
the walk names in a chain."""


def _hostile(held: object) -> object:
    """``held`` made of twins, all the way down: its type's twin, holding twins of its parts."""
    kind = type(held)
    if kind is bool or kind is range:
        return held
    if isinstance(held, memoryview):
        return memoryview(TWINS[bytearray](held.tobytes()))
    if isinstance(held, slice):
        return slice(_hostile(held.start), _hostile(held.stop), _hostile(held.step))
    if isinstance(held, io.StringIO):
        return TWINS[io.StringIO](held.getvalue())
    if isinstance(held, io.BytesIO):
        return TWINS[io.BytesIO](held.getvalue())
    if isinstance(held, array):
        with warnings.catch_warnings():  # array('u') is deprecated from 3.13
            warnings.simplefilter("ignore", DeprecationWarning)
            return TWINS[array](held.typecode, held)
    if isinstance(held, dict):
        return TWINS[dict]({_hostile(k): _hostile(v) for k, v in held.items()})
    for base in (list, tuple, deque):
        if isinstance(held, base):
            return TWINS[base](_hostile(element) for element in held)
    for base in (str, bytes, bytearray, int):
        if isinstance(held, base):
            return TWINS[base](held)
    return held


def _parted(held: object) -> object:
    """``held`` made of twins, under a twin of a module holding a twin dict that holds it: every
    hit's chain then names a twin parent (a module, a dict) as the walk describes one."""
    module = TWINS[types.ModuleType]("held")
    module.__dict__["held"] = TWINS[dict]({TWINS[str]("held"): _hostile(held)})
    return module


def _parts(held: object) -> list[object]:
    """``held`` and every part of it ``_hostile`` makes, as built (a twin's own base reads, run
    unarmed)."""
    found = [held]
    if isinstance(held, types.ModuleType):
        found += _parts(held.__dict__["held"])
    elif isinstance(held, memoryview):
        found.append(held.obj)
    elif isinstance(held, slice):
        found += [part for value in (held.start, held.stop, held.step) for part in _parts(value)]
    elif isinstance(held, dict):
        found += [part for item in dict.items(held) for value in item for part in _parts(value)]
    elif isinstance(held, list | tuple | deque):
        found += [part for element in held for part in _parts(element)]
    return found


UNTWINNED = (
    bool, range, memoryview, slice, type(None), type(memoryview(TWINS[bytearray](b"x")).obj)
)  # fmt: skip
"""What a twin cell holds that is not a twin: what the interpreter lets no class subclass, the
``None`` of a slice, and what a ``memoryview`` of a twin wraps the twin's ``__buffer__`` in."""


def _wanted(base: type, spared: frozenset[str]) -> set[str]:
    """The slots a twin of ``base`` must override, found apart from ``_slots`` (which a mistake
    in the generator would change): every name of ``base`` but ``spared`` that is callable or a
    data descriptor."""
    descriptors = types.GetSetDescriptorType | types.MemberDescriptorType
    return {
        name
        for name in dir(base)
        if name not in spared
        and (
            isinstance(inspect.getattr_static(base, name), descriptors)
            or callable(getattr(base, name))
        )
    }


def _checked() -> dict[str, list[str]]:
    """The generator, checked before the walk: every slot of each base (and of ``type``) is
    overridden, and one slot of each twin (and of its metaclass) records, and raises, armed."""
    bare: list[str] = []
    silent: list[str] = []
    for base, twin in [*TWINS.items(), (type, _Recorded)]:
        spared = META_SPARED if twin is _Recorded else SPARED
        bare += [f"{base.__name__}.{n}" for n in sorted(_wanted(base, spared) - set(vars(twin)))]
        sample = twin if twin is _Recorded else _sample(twin)
        slot = vars(twin)["__sizeof__"]  # read unarmed: a twin's class records its own lookups
        RAN.clear()
        ARMED.append(True)
        raised = False
        try:
            slot(sample)
        except RuntimeError:
            raised = True
        finally:
            ARMED.clear()
        expected = [f"{base.__name__}.__sizeof__"]
        if not raised or expected != RAN:
            silent.append(base.__name__)
    RAN.clear()
    return {"bare slots": bare, "silent twins": silent}


def _sample(twin: type) -> object:
    """An instance of a twin, made unarmed."""
    if issubclass(twin, array):
        return twin("b")
    if issubclass(twin, types.ModuleType):
        return twin("sample")
    return twin()


def twins(scan: Any) -> dict[str, Any]:
    """The generator checked; then the matrix again, every holder and every part of it a twin,
    under a twin module and a twin dict, armed for its walk; and what was not a twin."""
    checked = _checked()
    untwinned: set[str] = set()

    def convert(held: object) -> object:
        parted = _parted(held)
        untwinned.update(
            type(part).__name__
            for part in _parts(parted)
            if type(part) not in TWINS.values() and not isinstance(part, UNTWINNED)
        )
        return parted

    RAN.clear()
    try:
        found = matrix(scan, convert)
        error = None
    except RuntimeError as raised:
        found, error = {"cells": [], "missing": [], "armed": None}, str(raised)
    return {
        **checked,
        "armed": found["armed"],
        "untwinned": sorted(untwinned),
        "cells": len(found["cells"]),
        "missing": found["missing"],
        "ran": RAN,
        "error": error,
    }


def main() -> None:
    scan = _scan()
    found = {"matrix": matrix(scan), "routes": routes(scan), "order": order(scan)}
    print(json.dumps({**found, "hostile": hostile(scan), "twins": twins(scan)}))


if __name__ == "__main__":
    main()

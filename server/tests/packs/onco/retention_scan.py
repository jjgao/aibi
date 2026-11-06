"""The marker scan's child (M4.2a-1b, D438): what of a file is reachable after each call.

``test_retention`` runs this file in a fresh interpreter (``python -I``, the ``src`` of the tree
the suite imported first on its path), so that a memo of the first call, which an earlier test in
the suite's process would have filled, is the scan's own to fill. It makes nine calls through the
pack (``CALLS``), each with values of its own: two imports through ``PACK.importer``, two reads
through ``clinical.read``, an import refused at its last patient row (``EXCEL_DATE``), a read
refused at its end (``MISSING_FORM``), and three calls of the pack's other hooks, each on the
release of an import of its own: the validator's ``validate_descriptors`` (on the release, which
it accepts, and on the release with the dataset's ``type_of_cancer`` edited after the import to a
value that is not an OncoTree code, which it refuses: the meta file's parser refuses such a value
at the import, before the validator would see it, and then each ontology system on a marked
code, as the core asks of a code written), the catalogue's ``facet`` and the curation proposer
``propose``; each import's result is handed to ``validate_source``, as the core hands it. Every
entry point of the pack (``entry_points``, derived from ``Pack``'s fields and the protocols they
name) is driven (``DRIVEN``) or named with why it is not (``EXCLUDED``: none). Every value of
every call carries that call's marker, as ``FIELDS`` holds against what the pack's entry points
receive (``received``): random letters, in lower and upper case (a name is upper case, a dataset
id lower), and, for a missing spelling, which may hold no letter that is not the spelling's own,
a random run of ``[``, ``]`` and ``|`` (a spelling folds them away); what carries none carries
none by design, and ``FIELDS`` names it. After each call its result or refusal is dropped,
``gc.collect()`` runs, and **the heap is walked** for the markers of the calls made so far and a
canary, a dict's ``str`` key that every walk must see.

The walk starts from ``gc.get_objects()`` and ``sys.modules`` and follows each referent (but the
``_asyncio`` module's own, on every interpreter, its dict walked in their place: see
``_referents``), each dict's keys (a dict with only ``str`` keys has its values visited alone),
a ``range``'s parts, a class's names and a code object's constants, names and file (which no
traverse gives: ``CODE_READ``). The pins hold a route behind each type in ``PLAIN`` and each of
the three sequences, or name why none can be that type's alone, and a route for each part of a
code object it reads, for a code object nested in another, for a class's name and its qualified
name each alone and for a class a metaclass made; its leaves (``LEAVES``) are the five atoms. It
looks for a
marker's **form** (one of the three) as a class, **a form's code points occurring contiguously
and in order in one holder** (``Needles``):

- the characters of a ``str`` and of an ``io.StringIO``'s value, compared casefolded, so that any
  case of a form is found (a title-cased name), and reversed;
- the bytes of a ``bytes``, a ``bytearray``, a ``memoryview``, an ``io.BytesIO``'s value, an
  ``array`` of any type code and an ``int`` of 64 bits or more (its big-endian bytes, with the
  three zero bytes a number drops where a UTF-32 form begins, and which hold a little-endian
  one's reversed), in UTF-8, UTF-16 and UTF-32, little- and big-endian (ASCII case folded),
  reversed;
- the items of an ``array`` of any type code as the form's code points (``array(code,
  points)``'s bytes, built for every type code; native byte order);
- the elements, in order, of a ``list``, a ``tuple``, a ``deque`` and a dict's keys and its
  values, each a ``str`` (its characters: a form cut into chunks, or one character an element)
  or an ``int`` that is a code point;
- the hex of a form's UTF-8 (in text or bytes, any case) and its base64 (at each of the three
  alignments a byte string can put it at).

What is not read is everything not read above, for example: a holder that keeps a form's code
points apart or out of order (a ``set``, a ``frozenset``, the keys of ``dict.fromkeys(identifier)``,
which drop a repeated character); a nested holder (a list of one-character lists, a dict of
one-element tuples); a prefix shorter than the marker's length, a digest, a keyed hash, a
compression or a cipher of a form; another binary-to-text encoding (base32, base85, base64 wrapped
across lines, hex with separators), and the hex or base64 of an encoding other than UTF-8; an
``array`` of the other byte order; a number that is not a code point (a ``float`` element, a list
of floats, a ``Decimal``, a ``NUMBER`` cell); a list of the byte values of UTF-16 or UTF-32
(UTF-8's, for an ASCII form, are its code points); and a buffer of another exporter (a ``ctypes``
array, an ``mmap``); and any other object whose traverse does not give a ``str`` it holds (a
``datetime.timezone``'s name, a ``datetime`` carrying one), but a class's names and a code
object's parts.

The walk's own reads run no code of an object it walks: a type is told by its identity and by
C-level subtype checks, an object is read through its base type's own methods (``str.casefold``,
``list.__getitem__``, the unbound ``deque.__iter__``, ``int.__index__``, ``io.StringIO.getvalue``,
whose ``ValueError`` for a closed or uninitialised object is caught), a walked buffer is first
copied to an exact ``str``, ``bytes`` or ``bytearray`` by its base type's own method, and only
that copy is searched (a ``bytes`` subclass may define ``__buffer__``, which ``re`` would run),
and a name is used only when it is an exact ``str``, so a class that raises on a hash, a
comparison, a length, a format, an attribute or a buffer (the suite keeps some) is walked. The
walk's own objects (the markers, and every walk's needles and compiled patterns, which ``re``
caches: ``FINDERS``) are skipped by identity, and an element of a sequence that is one of them
reads as a break. What does
run code is not the walk's: the ``gc.collect()`` before it may run a finalizer or a weakref
callback, and the interpreter's C-level traverse of an object (``gc.get_objects``,
``gc.get_referents``) is the interpreter's own (issue #140). A hit's chain names only classes,
functions and modules (a ``__qualname__`` or a module's ``__name__``, when it is an exact
``str``); any other holder is named by its type. The report, one JSON line, says which walks ran
and what was reachable after which call, by kind and chain, never the matched content.

What it holds: retention that carries a marker in one of those reads, at the end of any call. What
no scan holds, and the mutants that live, are D438's.
"""

import dataclasses
import gc
import importlib
import importlib.util
import inspect
import io
import json
import os
import re
import secrets
import sys
import tempfile
import typing
import warnings
from array import array, typecodes
from base64 import b64encode
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePath
from types import (
    BuiltinFunctionType,
    CellType,
    CodeType,
    FunctionType,
    GetSetDescriptorType,
    MappingProxyType,
    MemberDescriptorType,
    MethodDescriptorType,
    MethodType,
    MethodWrapperType,
    ModuleType,
    WrapperDescriptorType,
)
from typing import Any, NamedTuple
from weakref import ReferenceType

LETTERS = "abcdefghjkmnpqrstuvwxyz"
MARKS = "[]|"
ROWS = 300
"""More rows than any bounded memo (a 128-entry memo is the usual one) holds."""
CHAIN = 12
CANARY: list[object] = []
"""Where ``main`` holds its canary (a heap object: a frame's locals are not)."""
FINDERS: list["Finder"] = []
"""Every walk's searches, kept so that every walk skips them: ``re`` caches a compiled pattern by
its source, which holds the needles (clearing the cache would clear what a call put there)."""
LEAVES = frozenset(id(kind) for kind in (float, bool, type(None), complex, type(...)))
"""The ids of the leaf types: never the types, which a set would hash and compare, and a type's
metaclass may run code for either."""
ENCODINGS = ("utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")
"""The encodings a form is looked for in, in a byte holder (no byte order mark: a form inside a
buffer has none, and one with a mark holds the bare form)."""
WIDE = 64
"""The bits from which an ``int`` is read as bytes: a form's shortest encoding is longer."""
_BIG = 1 << WIDE
_ALL = slice(None)
_TYPE_NAME = type.__dict__["__name__"].__get__
_TYPE_QUALNAME = type.__dict__["__qualname__"].__get__
_FUNCTION_QUALNAME = FunctionType.__dict__["__qualname__"].__get__
_STR_COPY: Callable[[Any, slice], str] = str.__getitem__
_STR_CASEFOLD: Callable[[str], str] = str.casefold
_BYTES_COPY: Callable[[Any, slice], bytes] = bytes.__getitem__
_BYTES_LOWER: Callable[[bytes], bytes] = bytes.lower
_BYTEARRAY_COPY: Callable[[Any, slice], bytearray] = bytearray.__getitem__
_MEMORYVIEW_TOBYTES: Callable[[Any], bytes] = memoryview.tobytes
_ARRAY_TYPECODE: Callable[[Any], str] = array.__dict__["typecode"].__get__
_ARRAY_TOBYTES: Callable[[Any], bytes] = array.tobytes
_STRINGIO_GETVALUE: Callable[[Any], str] = io.StringIO.getvalue
_BYTESIO_GETVALUE: Callable[[Any], bytes] = io.BytesIO.getvalue
_INT_INDEX: Callable[[Any], int] = int.__index__
_LIST_COPY: Callable[[Any, slice], list[object]] = list.__getitem__
_TUPLE_COPY: Callable[[Any, slice], tuple[object, ...]] = tuple.__getitem__
_DEQUE_ITER: Callable[[Any], Iterable[object]] = deque.__iter__
_DICT_KEYS: Callable[[Any], Iterable[object]] = dict.keys
_DICT_VALUES: Callable[[Any], Iterable[object]] = dict.values
_DICT_LEN: Callable[[Any], int] = dict.__len__
_RANGE_PARTS = tuple(range.__dict__[name].__get__ for name in ("start", "stop", "step"))
CODE_READ = (
    "co_consts", "co_names", "co_varnames", "co_freevars", "co_cellvars", "co_filename",
    "co_name", "co_qualname",
)  # fmt: skip
_CODE_PARTS = tuple(CodeType.__dict__[name].__get__ for name in CODE_READ)
"""What a code object holds, read through ``CodeType``'s own descriptors: a code object has no
traverse (``gc.get_referents`` gives nothing of it), and code compiled at call time (a
``namedtuple`` named by a call's value) holds that value in its constants and names."""


class Marker(NamedTuple):
    """One call's marker: ``lower`` (a dataset id, a file name, a value), ``upper`` (the same
    letters, as an attribute's name needs), ``marks`` (a missing spelling's)."""

    lower: str
    upper: str
    marks: str

    @staticmethod
    def new() -> "Marker":
        lower = "".join(secrets.choice(LETTERS) for _ in range(16))
        return Marker(lower, lower.upper(), "".join(secrets.choice(MARKS) for _ in range(20)))

    def forms(self) -> tuple[str, str, str]:
        return self.lower, self.upper, self.marks


def fresh(text: str) -> str:
    """A copy of ``text`` that is an object of its own, so that what a call keeps of what it was
    given is never the scan's own string."""
    return (text + "x")[:-1]


class Hit(NamedTuple):
    """A marker found: the call (its index), what read holds it, and the chain from a root."""

    call: int
    kind: str
    chain: tuple[str, ...]


# --- What the walk looks for ---------------------------------------------------------------------


def _base64(raw: bytes) -> list[bytes]:
    """The base64 of ``raw`` at each of the three alignments a longer byte string can put it at:
    the characters that encode ``raw``'s bytes alone (a group of three bytes is four characters,
    and a group that holds a byte of what is around it is dropped)."""
    found = []
    for before in range(3):
        encoded = b64encode(bytes(before) + raw)
        start = 1 if before else 0
        found.append(encoded[4 * start : 4 * ((before + len(raw)) // 3)])
    return found


def _points(code: str, form: str) -> bytes | None:
    """``form``'s code points as an ``array`` of ``code`` holds them (``None`` where one does not
    fit the type): ``array('u')`` is deprecated from 3.13, which warns when one is made."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            if code in "uw":
                return array(code, form).tobytes()
            points = [ord(character) for character in form]
            return array(code, [float(p) for p in points] if code in "fd" else points).tobytes()
        except OverflowError:
            return None


def _sorted(found: set[tuple[int, bytes]]) -> tuple[tuple[int, bytes], ...]:
    return tuple(sorted(found))


class Needles(NamedTuple):
    """What a walk looks for, each with the index of the marker it belongs to: ``text`` in a
    casefolded text, ``exact`` in a text as it is (base64), ``blobs`` in ASCII-lowered bytes (the
    encodings, reversed, and the hex), ``raw`` in bytes as they are (base64, and the code points
    of every ``array`` type code)."""

    text: tuple[tuple[int, str], ...]
    exact: tuple[tuple[int, str], ...]
    blobs: tuple[tuple[int, bytes], ...]
    raw: tuple[tuple[int, bytes], ...]

    @staticmethod
    def of(markers: Sequence[Marker]) -> "Needles":
        text: set[tuple[int, str]] = set()
        exact: set[tuple[int, str]] = set()
        blobs: set[tuple[int, bytes]] = set()
        raw: set[tuple[int, bytes]] = set()
        for index, marker in enumerate(markers):
            for form in marker.forms():
                folded = form.casefold()
                utf8 = form.encode()
                text |= {(index, folded), (index, folded[::-1]), (index, utf8.hex())}
                exact |= {(index, b.decode()) for b in _base64(utf8)}
                for encoding in ENCODINGS:
                    blob = folded.encode(encoding)
                    blobs |= {(index, blob), (index, blob[::-1])}
                blobs.add((index, utf8.hex().encode()))
                raw |= {(index, b) for b in _base64(utf8)}
                for code in typecodes:
                    for written in (form, folded):
                        points = _points(code, written)
                        if points is not None:
                            raw.add((index, points))
        return Needles(tuple(sorted(text)), tuple(sorted(exact)), *map(_sorted, (blobs, raw)))


class _Search(NamedTuple):
    """One kind of needle, compiled: ``candidate``, a run of the needles' own characters as long
    as the shortest of them (a text without one holds no needle, and it costs one pass whatever
    the number of markers), and ``needles``, any of them, with the needles by marker."""

    candidate: re.Pattern[Any]
    needles: re.Pattern[Any]
    found: tuple[tuple[int, Any], ...]

    @staticmethod
    def of(found: tuple[tuple[int, str], ...] | tuple[tuple[int, bytes], ...]) -> "_Search":
        values: list[Any] = [needle for _, needle in found]
        if not values:
            never = re.compile("(?!)")
            return _Search(never, never, found)
        candidate = _run(values)
        if type(values[0]) is str:
            needles = re.compile("|".join(re.escape(v) for v in sorted(values, key=len)[::-1]))
        else:
            needles = re.compile(b"|".join(re.escape(v) for v in sorted(values, key=len)[::-1]))
        assert all(candidate.search(value) and needles.search(value) for value in values)
        return _Search(candidate, needles, found)

    def __call__(self, data: Any) -> set[int]:
        """The markers whose needles ``data`` (an exact ``str`` or ``bytes``) holds."""
        if self.candidate.search(data) is None or self.needles.search(data) is None:
            return set()
        return {index for index, needle in self.found if needle in data}

    def own(self) -> list[object]:
        """The objects this search holds, for the walk to skip: the patterns, their sources,
        which ``re``'s cache holds as keys, and the needles."""
        found: list[object] = [self, self.found, *self.found]
        found += [needle for _, needle in self.found]
        for pattern in (self.candidate, self.needles):
            found += [pattern, pattern.pattern]
        return found


def _run(found: Iterable[Any], flags: int = 0) -> re.Pattern[Any]:
    """A run of the characters of ``found`` (texts, or byte strings), as long as the shortest."""
    values = list(found)
    shortest = min(len(value) for value in values)
    if type(values[0]) is str:
        chars = "".join(re.escape(c) for c in sorted(set("".join(values))))
        return re.compile(f"[{chars}]{{{shortest}}}", flags)
    chars_b = b"".join(re.escape(bytes([c])) for c in sorted(set(b"".join(values))))
    return re.compile(b"[" + chars_b + b"]{" + str(shortest).encode() + b"}", flags)


class Finder(NamedTuple):
    """The four searches of a walk, and every object they hold. ``ascii_text`` and ``bytes_any``
    are one search each for an ASCII text and for bytes, as they are, ASCII case ignored: a text
    or bytes without a run of them holds none of the needles of any search."""

    text: _Search
    exact: _Search
    blobs: _Search
    raw: _Search
    ascii_text: re.Pattern[str]
    bytes_any: re.Pattern[bytes]

    @staticmethod
    def of(markers: Sequence[Marker]) -> "Finder":
        needles = Needles.of(markers)
        text, exact, blobs, raw = (_Search.of(found) for found in needles)
        if not markers:
            return Finder(text, exact, blobs, raw, re.compile("(?!)"), re.compile(b"(?!)"))
        texts = [needle for _, needle in (*needles.text, *needles.exact)]
        buffers = [needle for _, needle in (*needles.blobs, *needles.raw)]
        ascii_text = _run(texts, re.IGNORECASE | re.ASCII)
        bytes_any = _run(buffers, re.IGNORECASE)
        assert all(ascii_text.search(value) for value in texts)
        assert all(bytes_any.search(value) for value in buffers)
        return Finder(text, exact, blobs, raw, ascii_text, bytes_any)

    def in_text(self, text: str) -> set[int]:
        """In an exact ``str``: its casefold, and itself."""
        if str.isascii(text) and self.ascii_text.search(text) is None:
            return set()
        return self.text(_STR_CASEFOLD(text)) | self.exact(text)

    def in_bytes(self, data: bytes) -> set[int]:
        """In an exact ``bytes``: its ASCII lower case, and itself."""
        if self.bytes_any.search(data) is None:
            return set()
        return self.blobs(_BYTES_LOWER(data)) | self.raw(data)

    def own(self) -> list[object]:
        found: list[object] = [self, *(part for search in self[:4] for part in search.own())]
        for pattern in self[4:]:
            found += [pattern, pattern.pattern]
        return found


# --- The walk ------------------------------------------------------------------------------------


def _module_name(module: object) -> object:
    """A module's ``__name__``, found by iterating its dict and comparing the keys that are exactly
    ``str``: a lookup would hash and compare a colliding key of another type."""
    for key, value in dict.items(vars(module)):
        if type(key) is str and str.__eq__(key, "__name__"):
            return value
    return None


def _type_name(kind: type) -> str:
    """A type's name through ``type``'s own descriptor, ``"?"`` where it is not an exact ``str``
    (a name may be set to a ``str`` subclass, whose format or comparison runs code)."""
    name = _TYPE_NAME(kind)
    return name if type(name) is str else "?"


def _describe(item: object) -> str:
    """A name for ``item`` in a chain, read without running its code: its type's name through
    ``type``'s own descriptor, and the name of a class, of a function or of a module through
    theirs, each used when it is an exact ``str``, never through ``getattr`` or ``len`` of the
    object, which a class of the pack may override. Other holders are named by their type."""
    kind = type(item)
    name: object = None
    if issubclass(kind, type):
        name = _TYPE_QUALNAME(item)
    elif kind is FunctionType:
        name = _FUNCTION_QUALNAME(item)
    elif kind is ModuleType:
        name = _module_name(item)
    if type(name) is str:
        return f"{_type_name(kind)} {name}"
    if issubclass(kind, dict):
        return f"dict of {_DICT_LEN(item)}"
    return _type_name(kind)


def _referents(item: object) -> list[object]:
    """What ``item`` refers to, for the walk, but for the ``_asyncio`` module, which is not asked,
    on every interpreter. CPython before 3.12.6 (3.12.3 to 3.12.5 checked; 3.12.6 is fixed) has
    that module's traverse visit its free list of ``FutureIter`` objects: dead ones, with a count
    of 0 and untracked. ``gc.get_referents`` hands them out, and the count falling back to 0 when
    the walk lets go of one frees it a second time, which links the free list into a cycle: the
    next traverse of the module never ends (gdb: ``PyList_Append`` called from the module's
    traverse), or the process faults. Any process that has awaited a future is exposed, as are the
    suite's ``asyncio`` tests (issue #140). On later interpreters asking is harmless but not
    needed, and the walk is the same everywhere. The module's own dict, which holds what it
    exports (``_current_tasks``, ``_scheduled_tasks``), is walked in its place, and that is how
    the current-tasks dict is seen. What the walk skips is the module's own C-level referents
    beyond its dict: its types, its functions, a constant tuple and the free list. None of them
    is state a call of a pack writes a value into, so none can hold a pack's marker."""
    name = _module_name(item) if type(item) is ModuleType else None
    if type(name) is str and str.__eq__(name, "_asyncio"):
        return [vars(item)]
    return gc.get_referents(item)


def _read(item: object, kind: type, find: Finder) -> tuple[str, set[int]] | None:
    """The markers a holder of text or bytes holds, with the read's name, or ``None`` for any
    other object. Every read is by the base type's own method, on a copy of an exact type where
    the object may be a subclass's."""
    if issubclass(kind, int):
        number = _INT_INDEX(item)
        if number.bit_length() < WIDE:
            return "int", set()
        number = -number if number < 0 else number
        size = (number.bit_length() + 7) // 8 + 3  # the zero bytes a UTF-32 form may begin with
        return "int", find.in_bytes(number.to_bytes(size, "big"))
    if issubclass(kind, str):
        return "str", find.in_text(_STR_COPY(item, _ALL))
    if issubclass(kind, bytes):
        return "bytes", find.in_bytes(_BYTES_COPY(item, _ALL))
    if issubclass(kind, bytearray):
        return "bytearray", find.in_bytes(bytes(_BYTEARRAY_COPY(item, _ALL)))
    if kind is memoryview:
        try:
            return "memoryview", find.in_bytes(_MEMORYVIEW_TOBYTES(item))
        except ValueError:  # released
            return "memoryview", set()
    if issubclass(kind, array):
        return f"array {_ARRAY_TYPECODE(item)}", find.in_bytes(_ARRAY_TOBYTES(item))
    if issubclass(kind, io.StringIO):
        try:
            return "StringIO", find.in_text(_STRINGIO_GETVALUE(item))
        except ValueError:  # closed, or never initialised
            return "StringIO", set()
    if issubclass(kind, io.BytesIO):
        try:
            return "BytesIO", find.in_bytes(_BYTESIO_GETVALUE(item))
        except ValueError:
            return "BytesIO", set()
    return None


def _units(items: Iterable[object], skip: set[int] | None = None) -> str:
    """The elements of a sequence as one text: a ``str``'s characters (a subclass's copied), an
    ``int`` that is a code point as its character, and anything else as a break, as is, where
    ``skip`` is given, an element that is one of the walk's own objects."""
    out: list[str] = []
    for element in items:
        kind = type(element)
        if skip is not None and id(element) in skip:
            out.append("\0")
        elif kind is str:
            out.append(element)  # type: ignore[arg-type]
        elif kind is int:
            out.append(chr(element) if 0 <= element <= 0x10FFFF else "\0")  # type: ignore[operator]
        elif issubclass(kind, str):
            out.append(_STR_COPY(element, _ALL))
        elif issubclass(kind, int) and kind is not bool:
            point = _INT_INDEX(element)
            out.append(chr(point) if 0 <= point <= 0x10FFFF else "\0")
        else:
            out.append("\0")
    return "".join(out)


def _in_sequence(elements: Any, find: Finder, skip: set[int]) -> set[int]:
    """The markers the elements of a sequence (a ``list``, a ``tuple``, a dict's view, each of an
    exact type) hold in order, by ``_units``. The elements' types are listed by identity first (a
    type is never hashed): a sequence with no ``str`` or ``int`` holds no text, and one of exact
    ``str`` alone is joined by ``str.join``. Where the text holds a needle, it is made again with
    the walk's own objects as breaks, which is the answer."""
    kinds = list(map(type, elements))
    distinct = dict(zip(map(id, kinds), kinds, strict=True))
    if len(distinct) == 1 and id(str) in distinct:
        text = "".join(elements)
    elif any(issubclass(kind, str | int) and kind is not bool for kind in distinct.values()):
        text = _units(elements)
    else:
        return set()
    if not find.in_text(text):
        return set()
    return find.in_text(_units(elements, skip))


def _sequences(item: object, kind: type, keys: bool) -> list[tuple[str, Any]]:
    """The ordered elements of a ``list``, a ``tuple``, a ``deque`` or a dict (its keys, where
    ``keys``, and its values), each by its base type's own method, with the read's name."""
    if issubclass(kind, dict):
        found: list[tuple[str, Any]] = [("dict values", _DICT_VALUES(item))]
        return [("dict keys", _DICT_KEYS(item)), *found] if keys else found
    if issubclass(kind, list):
        return [("list items", _LIST_COPY(item, _ALL))]
    if issubclass(kind, tuple):
        return [("tuple items", _TUPLE_COPY(item, _ALL))]
    if issubclass(kind, deque):
        return [("deque items", list(_DEQUE_ITER(item)))]
    return []


PLAIN = frozenset(
    id(kind)
    for kind in (
        type,
        FunctionType,
        CodeType,
        CellType,
        MethodType,
        BuiltinFunctionType,
        WrapperDescriptorType,
        MethodDescriptorType,
        MethodWrapperType,
        GetSetDescriptorType,
        MemberDescriptorType,
        MappingProxyType,
        property,
        staticmethod,
        classmethod,
        set,
        frozenset,
        ReferenceType,
    )
)
"""The ids of the exact types the walk only follows: none of them is a type it reads, or one a
sequence's elements are read in order from (a set's are in no order)."""


def reachable(
    markers: Sequence[Marker], ignore: Iterable[object] = (), *, keys: bool = True
) -> list[Hit]:
    """Every holder in the heap that holds a form of one of ``markers`` (the index of the marker
    is the call's), by the reads of the module's docstring, but for the markers' own objects, the
    walk's and ``ignore`` (the scan's other objects, by identity). ``keys=False`` is the walk that
    skips a dict's keys: a mutant of the scan's own, which its unit test must refuse."""
    modules = sys.modules
    if type(modules) is not dict:
        raise RuntimeError(
            f"sys.modules is a {_type_name(type(modules))}, not a dict: the walk cannot read it "
            "without running its code"
        )
    find = Finder.of(markers)
    FINDERS.append(find)
    gc.collect()  # after the patterns are compiled: what compiling them leaves is collected
    own = [markers, *markers, *(form for marker in markers for form in marker.forms())]
    own += [FINDERS, *(part for finder in FINDERS for part in finder.own())]
    skip = {id(obj) for obj in [*ignore, *own]}
    stack: list[object] = [*gc.get_objects(), *dict.values(modules)]
    parents: dict[int, object] = {}
    seen = set(skip)
    hits: list[Hit] = []

    def found(matched: set[int], name: str, item: object) -> None:
        for index in sorted(matched):
            chain: list[str] = [name]
            link: object | None = item
            while link is not None and len(chain) < CHAIN:
                link = parents.get(id(link))
                if link is not None:
                    chain.append(_describe(link))
            hits.append(Hit(index, name, tuple(chain)))

    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        kind = type(item)
        if kind is str:
            if matched := find.in_text(item):  # type: ignore[arg-type]
                found(matched, "str", item)
            continue
        if kind is int:
            if -_BIG < item < _BIG:  # type: ignore[operator]
                continue
        elif kind is not bytes and id(kind) in LEAVES:
            continue
        if kind is int or kind is bytes:
            read = _read(item, kind, find)
            if read is not None and read[1]:
                found(read[1], read[0], item)
            continue
        if id(kind) in PLAIN:
            held = gc.get_referents(item)
            if kind is type:
                held += [_TYPE_NAME(item), _TYPE_QUALNAME(item)]
            elif kind is CodeType:  # the parts' elements: no name is read joined to the next
                for part in _CODE_PARTS:
                    found_part = part(item)
                    held += found_part if type(found_part) is tuple else [found_part]
        elif kind is tuple or kind is list or kind is dict:
            held = gc.get_referents(item)
            if kind is dict and keys:
                held += _DICT_KEYS(item)
            for name, elements in _sequences(item, kind, keys):
                if len(elements) > 1 and (matched := _in_sequence(elements, find, skip)):
                    found(matched, name, item)
        else:
            read = _read(item, kind, find)
            if read is not None and read[1]:
                found(read[1], read[0], item)
            held = _referents(item)
            if issubclass(kind, type):  # a class's names, which its traverse does not give
                held += [_TYPE_NAME(item), _TYPE_QUALNAME(item)]
            if keys and issubclass(kind, dict):
                held += _DICT_KEYS(item)
            elif kind is range:
                held += [part(item) for part in _RANGE_PARTS]
            for name, elements in _sequences(item, kind, keys):
                if len(elements) > 1 and (matched := _in_sequence(elements, find, skip)):
                    found(matched, name, item)
        for child in held:
            parents.setdefault(id(child), item)
        stack.extend(held)
    return hits


# --- The studies ---------------------------------------------------------------------------------

AT = "2026-01-01T00:00:00Z"


def _header(names: list[str], types: list[str], marker: Marker) -> str:
    labels = "\t".join(f"Label {marker.upper} {n}" for n in names)
    definitions = "\t".join(f"Definition {marker.lower} of {n}" for n in names)
    rows = [labels, definitions, "\t".join(types), "\t".join("1" for _ in names)]
    return "".join(f"#{row}\n" for row in rows) + "\t".join(names) + "\n"


def _base3(number: int, digits: int) -> str:
    out = []
    for _ in range(digits):
        number, rest = divmod(number, 3)
        out.append(MARKS[rest])
    return "".join(out)


def patient_file(marker: Marker, *, late: str | None = None, overflow: bool = False) -> bytes:
    """A patient file of ``ROWS`` rows, a value of the marker in every column: the key, a STRING,
    a NUMBER (one cell in two is not a number), the OS, DFS and PFS statuses (the last with its
    label), a BOOLEAN and a STRING with missing spellings, and three columns whose names carry
    the marker. ``late`` is the last row's STRING cell; with ``overflow`` every row's missing
    spelling is its own, which a column may not have so many of."""
    lo, up, marks = marker
    extra = [f"C_{up}_{k}" for k in range(3)]
    names = ["PATIENT_ID", "NOTE", "SCORE", "OS_STATUS", "DFS_STATUS", "PFS_STATUS", "PFS_MONTHS"]
    names += ["SMOKER", "MISS", *extra]
    types = ["STRING", "STRING", "NUMBER", "STRING", "STRING", "STRING", "NUMBER", "BOOLEAN"]
    types += ["STRING", "STRING", "STRING", "STRING"]
    out = [_header(names, types, marker)]
    for i in range(ROWS):
        note = late if late is not None and i == ROWS - 1 else f"n{i}{lo}"
        spelling = f"N{marks}{_base3(i, 6)}A" if overflow else f"[N{marks}A]"
        row = [f"P{i}{up}", note, f"{i}.5" if i % 2 else f"Q{up}"]
        row += [("0:LIVING", "1:DECEASED")[i % 2], ("0:DiseaseFree", "1:Recurred")[i % 2]]
        row += [f"{i % 2}:L{up}{i}", f"{i}.75", ("TRUE", "FALSE", f"N{marks}A")[i % 3]]
        row += [spelling if overflow or i % 5 == 0 else f"m{i}{lo}"]
        row += [f"w{k}{i}{lo}" for k in range(3)]
        out.append("\t".join(row) + "\n")
    return "".join(out).encode()


def sample_file(marker: Marker) -> bytes:
    lo, up, _ = marker
    names = ["PATIENT_ID", "SAMPLE_ID", "TYPE", "TMB", f"S_{up}"]
    out = [_header(names, ["STRING", "STRING", "STRING", "NUMBER", "STRING"], marker)]
    out += (f"P{i}{up}\tS{i}{up}\tt{i % 3}{lo}\t{i}.5\ts{i}{lo}\n" for i in range(ROWS))
    return "".join(out).encode()


def study(root: Path, marker: Marker, patient: bytes) -> Path:
    """A study directory whose every meta value, file name and directory name carries the marker,
    but for cBioPortal's vocabulary (``FIELDS``): its ``type_of_cancer`` is an OncoTree code by
    its form, as the validator reads it."""
    lo, up, _ = marker
    directory = root / f"s{lo}"
    directory.mkdir()
    meta = {
        "meta_study.txt": (
            f"type_of_cancer: t{lo}\ncancer_study_identifier: m_{lo}\nname: Name {up}\n"
            f"description: Description {lo}\ncitation: Citation {up}\ngroups: Group{up}\n"
            f"short_name: S {lo}\npmid: 12345\nreference_genome: hg19\n"
        ),
        "meta_clinical_patient.txt": (
            f"cancer_study_identifier: m_{lo}\ngenetic_alteration_type: CLINICAL\n"
            f"datatype: PATIENT_ATTRIBUTES\ndata_filename: patient_{lo}.txt\n"
        ),
        "meta_clinical_sample.txt": (
            f"cancer_study_identifier: m_{lo}\ngenetic_alteration_type: CLINICAL\n"
            f"datatype: SAMPLE_ATTRIBUTES\ndata_filename: sample_{lo}.txt\n"
        ),
    }
    for name, text in meta.items():
        (directory / name).write_text(text, encoding="utf-8")
    (directory / f"patient_{lo}.txt").write_bytes(patient)
    (directory / f"sample_{lo}.txt").write_bytes(sample_file(marker))
    return directory


# --- The calls -----------------------------------------------------------------------------------


class Call(NamedTuple):
    """One call of the scan: ``kind`` (``import``, ``read``, ``validate``, ``facet`` or
    ``propose``; the last three import a study of their own first and call the hook on its
    release), whether an import names its dataset (``Name <upper>``; ``None`` otherwise),
    ``late`` (the patient file's last STRING cell, a date the reader refuses there) and
    ``overflow`` (a missing spelling a row, more than a column may have)."""

    kind: str
    named: bool = False
    late: str | None = None
    overflow: bool = False


CALLS = (
    Call("import", named=True),
    Call("import"),
    Call("read"),
    Call("read"),
    Call("import", late="Mar-1"),
    Call("read", overflow=True),
    Call("validate", named=True),
    Call("facet", named=True),
    Call("propose", named=True),
)


def patient_of(marker: Marker, call: Call) -> bytes:
    return patient_file(marker, late=call.late, overflow=call.overflow)


def name_of(marker: Marker, call: Call) -> str | None:
    return f"Name {marker.upper}" if call.named else None


def edited_of(marker: Marker) -> str:
    """The ``type_of_cancer`` the validator's call edits the dataset's to: not an OncoTree code by
    its form (a space, a ``!``)."""
    return f"t {marker.lower} !"


def read_of(marker: Marker, call: Call) -> tuple[tuple[bytes, str], tuple[bytes, str]]:
    """What a read gives ``clinical.read``: the patient file and its name, the sample file and
    its name."""
    patient = (patient_of(marker, call), f"patient_{marker.lower}.txt")
    return patient, (sample_file(marker), f"sample_{marker.lower}.txt")


@dataclass(frozen=True)
class Release:
    """A release's view, as the core gives a hook one (``pack_api.ReleaseView``)."""

    descriptors: Mapping[str, Any]
    dataset: str
    manifest: str = "sha256:" + "0" * 64
    label: int = 1
    packs: Sequence[str] = ("onco",)


def _nothing(done: int) -> None:
    """What ``drive`` does after a call when nothing is asked of it."""


def drive(calls: Sequence[Callable[[], str]], after: Callable[[int], None] = _nothing) -> list[str]:
    """Each call in turn, its outcome kept and everything else of it dropped: then ``gc.collect()``
    and ``after`` with the number of calls made (the walk, in ``main``; a test gives calls of its
    own, through this loop)."""
    outcomes: list[str] = []
    for done, call in enumerate(calls, 1):
        outcomes.append(call())
        gc.collect()
        after(done)
    return outcomes


def run(
    root: Path,
    markers: Sequence[Marker],
    after: Callable[[int], None] = _nothing,
) -> list[str]:
    """``CALLS``, each an outcome (``ok``, or the refusal's code), and nothing held by this
    function's frame between them."""
    from aibi.core.importers.confine import Confinement
    from aibi.core.schema.descriptors import DatasetDescriptor
    from aibi.core.schema.limits import ImportLimits
    from aibi.core.schema.pack_api import ImportOptions, Refused
    from aibi.packs.onco import clinical

    pack: Any = importlib.import_module("aibi.packs.onco").PACK
    limits = ImportLimits()

    def imported(marker: Marker, call: Call) -> Any:
        directory = study(root, marker, patient_of(marker, call))
        reader = Confinement.of(directory.parent)
        options = ImportOptions(
            dataset=fresh(marker.lower),
            reader=reader,
            limits=limits,
            at=AT,
            name=name_of(marker, call),
        )
        source = reader.confine(directory)
        result = pack.importer.import_source(source, options)
        assert len(result.descriptors) > 10
        assert pack.validator.validate_source(source, result) == []  # as the core does
        return result

    def release(marker: Marker, call: Call) -> Release:
        found = imported(marker, call).descriptors
        return Release({d.id: d for d in found}, fresh(marker.lower))

    def reading(marker: Marker, call: Call) -> None:
        (patient, patient_name), (sample, sample_name) = read_of(marker, call)
        found = clinical.read(patient, patient_name, "patient", limits)
        clinical.read(
            sample, sample_name, "sample", limits, cells_before=found.cells, patient=found
        )

    def validating(marker: Marker, call: Call) -> str:
        view = release(marker, call)
        assert pack.validator.validate_descriptors(view) == []
        dumped = view.descriptors["dataset"].model_dump(mode="json")
        dumped["extensions"]["onco"]["type_of_cancer"] = edited_of(marker)
        edited = Release(
            {**view.descriptors, "dataset": DatasetDescriptor.model_validate(dumped)},
            view.dataset,
        )
        [refusal] = pack.validator.validate_descriptors(edited)
        for system in pack.ontology_systems.values():  # as the core does a written code's
            system(fresh(f"t{marker.lower}"))
        return str(refusal.code)

    def faceting(marker: Marker, call: Call) -> None:
        found = pack.facet(release(marker, call))
        assert found["type_of_cancer"] == [f"t{marker.lower}"], found

    def proposing(marker: Marker, call: Call) -> None:
        assert len(pack.proposer(release(marker, call))) == 1

    def outcome(marker: Marker, call: Call) -> str:
        try:
            if call.kind == "import":
                imported(marker, call)
            elif call.kind == "read":
                reading(marker, call)
            elif call.kind == "validate":
                return validating(marker, call).removeprefix("onco.")
            elif call.kind == "facet":
                faceting(marker, call)
            else:
                proposing(marker, call)
        except Refused as error:
            code = str(error.refusals[0].code).removeprefix("onco.")
            error.with_traceback(None)
            return code
        return "ok"

    assert len(markers) == len(CALLS)
    calls = [
        (lambda marker=marker, call=call: outcome(marker, call))
        for marker, call in zip(markers, CALLS, strict=True)
    ]
    return drive(calls, after)


# --- What each call hands the pack, and which of it carries no marker -----------------------------


RELEASE = {
    "dataset": frozenset(),
    "descriptors[*]": frozenset({"the import's descriptor"}),
    "label": frozenset({"#"}),
    "manifest": frozenset({"sha#:#"}),
    "packs[*]": frozenset({"onco"}),
}
FIELDS: Mapping[str, frozenset[str]] = {
    "meta_study.txt: type_of_cancer": frozenset(),
    "meta_study.txt: cancer_study_identifier": frozenset(),
    "meta_study.txt: name": frozenset(),
    "meta_study.txt: description": frozenset(),
    "meta_study.txt: citation": frozenset(),
    "meta_study.txt: groups": frozenset(),
    "meta_study.txt: short_name": frozenset(),
    "meta_study.txt: pmid": frozenset({"#"}),
    "meta_study.txt: reference_genome": frozenset({"hg#"}),
    "meta_clinical_patient.txt: cancer_study_identifier": frozenset(),
    "meta_clinical_patient.txt: genetic_alteration_type": frozenset({"CLINICAL"}),
    "meta_clinical_patient.txt: datatype": frozenset({"PATIENT_ATTRIBUTES"}),
    "meta_clinical_patient.txt: data_filename": frozenset(),
    "meta_clinical_sample.txt: cancer_study_identifier": frozenset(),
    "meta_clinical_sample.txt: genetic_alteration_type": frozenset({"CLINICAL"}),
    "meta_clinical_sample.txt: datatype": frozenset({"SAMPLE_ATTRIBUTES"}),
    "meta_clinical_sample.txt: data_filename": frozenset(),
    "patient: label": frozenset(),
    "patient: definition": frozenset(),
    "patient: type": frozenset({"STRING", "NUMBER", "BOOLEAN"}),
    "patient: priority": frozenset({"#"}),
    "patient: column name": frozenset(
        {"PATIENT_ID", "NOTE", "SCORE", "OS_STATUS", "DFS_STATUS", "PFS_STATUS", "PFS_MONTHS"}
        | {"SMOKER", "MISS"}
    ),
    "patient: PATIENT_ID": frozenset(),
    "patient: NOTE": frozenset({"Mar-#"}),
    "patient: SCORE": frozenset({"#.#"}),
    "patient: OS_STATUS": frozenset({"#:LIVING", "#:DECEASED"}),
    "patient: DFS_STATUS": frozenset({"#:DiseaseFree", "#:Recurred"}),
    "patient: PFS_STATUS": frozenset(),
    "patient: PFS_MONTHS": frozenset({"#.#"}),
    "patient: SMOKER": frozenset({"TRUE", "FALSE"}),
    "patient: MISS": frozenset(),
    "patient: C_{up}_#": frozenset(),
    "sample: label": frozenset(),
    "sample: definition": frozenset(),
    "sample: type": frozenset({"STRING", "NUMBER"}),
    "sample: priority": frozenset({"#"}),
    "sample: column name": frozenset({"PATIENT_ID", "SAMPLE_ID", "TYPE", "TMB"}),
    "sample: PATIENT_ID": frozenset(),
    "sample: SAMPLE_ID": frozenset(),
    "sample: TYPE": frozenset(),
    "sample: TMB": frozenset({"#.#"}),
    "sample: S_{up}": frozenset(),
    "a study's file name": frozenset(
        {"meta_study.txt", "meta_clinical_patient.txt", "meta_clinical_sample.txt"}
    ),
    "a study's directory name": frozenset(),
    "importer.import_source's options.dataset": frozenset(),
    "importer.import_source's options.name": frozenset({"None"}),
    "importer.import_source's options.at": frozenset({"#-#-#T#:#:#Z"}),
    "importer.import_source's options.original_name": frozenset({"None"}),
    "importer.import_source's options.previous": frozenset({"None"}),
    "validator.validate_source's result": frozenset({"the import's result"}),
    "clinical.read's name": frozenset(),
    "clinical.read's role": frozenset({"patient", "sample"}),
    "clinical.read's cells_before": frozenset({"#"}),
    "clinical.read's patient": frozenset({"None", "a read's result"}),
    **{
        f"{hook}'s release.{name}": shapes
        for hook in ("validator.validate_descriptors", "facet", "proposer")
        for name, shapes in RELEASE.items()
    },
    "validator.validate_descriptors's release.descriptors[dataset].extensions.onco"
    ".type_of_cancer": frozenset(),
    **{f"ontology_systems[{name}]'s code": frozenset() for name in ("OncoTree", "HGNC")},
    **{f"ontology_systems[{name}]'s code": frozenset() for name in ("NCBIGene", "SO")},
}
"""Each field of what a call hands the pack, as its entry points receive it (``received``): every
argument but the operator's (``OPERATOR``), a study's files read whole, a data file by its rows,
a dataclass by its fields, a release by its fields and its descriptors (each by what differs from
the import's own descriptor, which it was made from), with the shapes (each run of digits a
``#``) of its values that carry no marker, which every other value of it does: cBioPortal's
vocabulary (the meta files' names and kinds, the reference genome, the columns' types, the
predefined columns' names, the OS and DFS statuses' values, a BOOLEAN's ``TRUE`` and ``FALSE``),
numbers (a PubMed id, a priority, a NUMBER cell, a count of cells), the import's time, the import
that names no dataset (``None``: the second and the fifth call), the options no call gives (an
upload's file name, a previous release), the late date (the fifth call's refused cell), what an
entry point returned before in the call and is handed back (the import's result, its descriptors,
the patient file's read), and a release's manifest, label and packs, which the scan makes. Its
keys are the fields the pack receives, and the shapes the ones it receives: a field added
unmarked, a value that loses its marker, or a call site that hands a value of its own (an id, a
name, a descriptor changed), fails ``test_every_value_of_a_call_carries_its_marker``."""


def _shape(text: str, marker: Marker) -> str:
    """``text`` with each form of ``marker`` named and each run of digits a ``#``."""
    for form, name in zip(marker.forms(), ("{lo}", "{up}", "{marks}"), strict=True):
        text = text.replace(form, name)
    return re.sub(r"[0-9]+", "#", text)


def _data(role: str, data: bytes, out: dict[str, list[str]]) -> None:
    """A data file's values, by field: its four header rows, its column names, and each
    column's cells (the field named by the column's name, which ``unmarked`` shapes)."""
    lines = data.decode().splitlines()
    rows = [line.removeprefix("#").split("\t") for line in lines[:4]]
    for field, row in zip(("label", "definition", "type", "priority"), rows, strict=True):
        out.setdefault(f"{role}: {field}", []).extend(row)
    names = lines[4].split("\t")
    out.setdefault(f"{role}: column name", []).extend(names)
    for line in lines[5:]:
        for name, value in zip(names, line.split("\t"), strict=True):
            out.setdefault(f"{role}: {name}", []).append(value)


def _study(directory: Path, out: dict[str, list[str]]) -> None:
    """A study directory's values, by field: each file's name, each meta file's values, each data
    file's header rows, column names and cells, and the directory's name."""
    out.setdefault("a study's directory name", []).append(directory.name)
    for path in sorted(directory.iterdir()):
        out.setdefault("a study's file name", []).append(path.name)
        if path.name.startswith("meta_"):
            for line in path.read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition(": ")
                out.setdefault(f"{path.name}: {key}", []).append(value)
        else:
            _data(path.name.split("_")[0], path.read_bytes(), out)


OPERATOR = frozenset({"reader", "limits"})
"""What an entry point is given that is the operator's or the core's, never a value of a study:
the reader the core confines, and the limits. Every other argument is recorded."""
DRIVEN = frozenset(
    {"importer.import_source", "validator.validate_source", "validator.validate_descriptors"}
    | {"facet", "proposer"}
    | {f"ontology_systems[{name}]" for name in ("OncoTree", "HGNC", "NCBIGene", "SO")}
)
"""Every entry point of the pack (``entry_points``) that ``run`` drives: all of them."""
EXCLUDED: Mapping[str, str] = {}
"""The entry points of the pack ``run`` does not drive, each with why: none."""


def _protocols(hint: Any) -> list[type]:
    """The protocols a type hint names, through its unions and generic arguments."""
    found: list[type] = [hint] if getattr(hint, "_is_protocol", False) else []
    return found + [p for arg in typing.get_args(hint) for p in _protocols(arg)]


def entry_points(pack: Any) -> dict[str, tuple[Any, str, tuple[object, ...]]]:
    """Every entry point of ``pack`` the core may call, derived from ``Pack``'s fields: each
    method of the protocols a field's type names, for each object the field holds, and each
    callable a field holds (itself, or in a mapping or a sequence). By name: the object it is a
    method of (or ``pack``), the field, and its key in the field's mapping or sequence."""
    from aibi.core.schema.pack_api import Pack

    hints = typing.get_type_hints(Pack)
    found: dict[str, tuple[Any, str, tuple[object, ...]]] = {}
    for field in dataclasses.fields(Pack):
        value = getattr(pack, field.name)
        if isinstance(value, Mapping):
            items: list[tuple[tuple[object, ...], object]] = [((k,), v) for k, v in value.items()]
        elif isinstance(value, list | tuple):
            items = [((i,), v) for i, v in enumerate(value)]
        else:
            items = [((), value)]
        protocols = _protocols(hints[field.name])
        for key, item in items:
            label = field.name + "".join(f"[{k}]" for k in key)
            if item is None:
                continue
            if protocols:
                for protocol in protocols:
                    for method, member in vars(protocol).items():
                        if not method.startswith("_") and callable(member):
                            found[f"{label}.{method}"] = (item, method, ())
            elif callable(item) and not isinstance(item, type):
                found[label] = (pack, field.name, key)
    return found


def _install(pack: Any, owner: Any, name: str, key: tuple[object, ...], entry: object) -> None:
    """``entry`` in place of an entry point: a method set on its object, or a field of the frozen
    ``pack`` (in its mapping or sequence) set anew."""
    if owner is not pack:
        setattr(owner, name, entry)
        return
    value = getattr(pack, name)
    if key:
        if isinstance(value, Mapping):
            value = MappingProxyType({**value, key[0]: entry})
        else:
            value = type(value)(entry if i == key[0] else v for i, v in enumerate(value))
    else:
        value = entry
    object.__setattr__(pack, name, value)  # Pack is a frozen dataclass


class _Recorder:
    """What the pack's entry points receive, by field, for one call; ``own`` names what an entry
    point returned before in the call (an import's result and its descriptors, a read's result),
    which a later one is handed back."""

    def __init__(self) -> None:
        self.fields: dict[str, list[str]] = {}
        self.own: dict[int, tuple[str, object]] = {}
        self.imported: dict[str, Any] = {}
        self.called: set[str] = set()

    def reset(self) -> None:
        """Forget the call's record (the entry points called stay with the caller)."""
        self.fields, self.own, self.imported = {}, {}, {}
        self.called = set()

    def returned(self, entry: str, value: Any) -> None:
        if entry == "importer.import_source":
            self.own[id(value)] = ("the import's result", value)
            self.imported = {descriptor.id: descriptor for descriptor in value.descriptors}
            for descriptor in value.descriptors:
                self.own[id(descriptor)] = ("the import's descriptor", descriptor)
        elif entry == "clinical.read":
            self.own[id(value)] = ("a read's result", value)

    def arguments(self, entry: str, given: Mapping[str, Any]) -> None:
        self.called.add(entry)
        role = given.get("role")
        for name, value in given.items():
            if name not in OPERATOR:
                self.leaf(f"{entry}'s {name}", value, role)

    def add(self, path: str, value: str) -> None:
        self.fields.setdefault(path, []).append(value)

    def leaf(self, path: str, value: Any, role: object = None) -> None:
        """Every leaf of ``value``, by its type: a ``str`` or a number as it is; bytes as a data
        file of ``role``; a path as a study directory; a dataclass by its fields (``OPERATOR``
        aside); a mapping or a sequence by its items (what an entry point returned before, under
        ``[*]``); a model the import made, by what differs from it; anything else by its type."""
        kind = type(value)
        if id(value) in self.own:
            self.add(path, self.own[id(value)][0])
        elif kind is str:
            self.add(path, value)
        elif kind in (int, float, bool, type(None)):
            self.add(path, str(value))
        elif kind is bytes:
            _data(str(role or path), value, self.fields)
        elif isinstance(value, PurePath):
            _study(Path(value), self.fields)
        elif dataclasses.is_dataclass(value) and not isinstance(value, type):
            for field in dataclasses.fields(value):
                if field.name not in OPERATOR:
                    self.leaf(f"{path}.{field.name}", getattr(value, field.name), role)
        elif isinstance(value, Mapping | list | tuple):
            items = value.items() if isinstance(value, Mapping) else enumerate(value)
            for key, item in items:
                at = "*" if id(item) in self.own or not isinstance(value, Mapping) else key
                self.leaf(f"{path}[{at}]", item, role)
        elif hasattr(value, "model_dump"):
            model: Any = value
            made = self.imported.get(str(getattr(model, "id", "")))
            before = made.model_dump(mode="json") if made is not None else {}
            _differing(path, model.model_dump(mode="json"), before, self.fields)
        else:
            self.add(path, f"a {kind.__name__}")


def _differing(path: str, value: Any, before: Any, out: dict[str, list[str]]) -> None:
    """The leaves of ``value`` (a model's JSON) that differ from ``before``'s, by path."""
    if value == before:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            was = before.get(key) if isinstance(before, dict) else None
            _differing(f"{path}.{key}", item, was, out)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            was = before[index] if isinstance(before, list) and index < len(before) else None
            _differing(f"{path}[*]", item, was, out)
    else:
        out.setdefault(path, []).append(str(value))


@contextmanager
def received(recorder: _Recorder) -> Iterator[None]:
    """Every entry point of the pack (``entry_points``) and ``clinical.read``, which ``run`` calls
    directly, each wrapped so that it gives ``recorder`` every argument it receives, as it receives
    it (``inspect`` binds them), and what it returns, and put back on leaving: what the pack is
    handed, recorded at the callee (the marker table's test; the scan records nothing, which would
    hold it)."""
    pack = importlib.import_module("aibi.packs.onco").PACK
    clinical: Any = importlib.import_module("aibi.packs.onco.clinical")

    def wrap(entry: str, original: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(original)

        def called(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            recorder.arguments(entry, bound.arguments)
            value = original(*args, **kwargs)
            recorder.returned(entry, value)
            return value

        return called

    points = entry_points(pack)
    fields = {name: getattr(pack, name) for name in (f.name for f in dataclasses.fields(pack))}
    read = clinical.read
    for entry, (owner, name, key) in points.items():
        original = getattr(owner, name) if owner is not pack or not key else getattr(pack, name)
        _install(pack, owner, name, key, wrap(entry, original if not key else original[key[0]]))
    clinical.read = wrap("clinical.read", read)
    try:
        yield
    finally:
        clinical.read = read
        for owner, name, _ in points.values():
            if owner is not pack and name in vars(owner):
                delattr(owner, name)
        for name, value in fields.items():
            object.__setattr__(pack, name, value)


def handed(root: Path) -> tuple[dict[str, frozenset[str]], frozenset[str]]:
    """``run``'s calls, each with a marker of its own and with no walk, every entry point of the
    pack recording what it receives (``received``): each field, with the shapes of its values
    that carry no form of their call's marker; and the entry points called."""
    markers = [Marker.new() for _ in CALLS]
    found: dict[str, set[str]] = {}
    called: set[str] = set()
    recorder = _Recorder()

    def after(done: int) -> None:
        marker = markers[done - 1]
        for field, values in recorder.fields.items():
            shapes = found.setdefault(_shape(field, marker), set())
            shapes.update(
                _shape(value, marker)
                for value in values
                if not any(form in value for form in marker.forms())
            )
        called.update(recorder.called)
        recorder.reset()

    with received(recorder):
        run(root, markers, after)
    return {field: frozenset(shapes) for field, shapes in found.items()}, frozenset(called)


def unmarked(root: Path) -> dict[str, frozenset[str]]:
    """``handed``'s fields alone."""
    return handed(root)[0]


# --- The report ----------------------------------------------------------------------------------


STATE = Path(__file__).resolve().with_name("retention_state.py")


def state_guards() -> Any:
    """``retention_state.py``, loaded by its path (no module of the pack is imported by it)."""
    spec = importlib.util.spec_from_file_location("retention_state", STATE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    fresh_start = "aibi.packs.onco" not in sys.modules
    state = state_guards()
    guard = state.Guard(state.pack_modules())  # before the first call: its rejects and snapshot
    markers = [Marker.new() for _ in CALLS]
    canary = Marker.new()
    CANARY.append({fresh(canary.lower) + "k": 1})  # a str-keyed dict holding the canary as a key
    hits: list[dict[str, object]] = []
    walks: list[bool] = []
    stores = guard.own()
    ignored: list[bool] = []

    def after(done: int) -> None:
        """The state guards after call ``done`` (the allow-list, and the snapshot against the
        first), then the walk: the markers of the calls made so far, and the canary; the walk
        skips the guards' stores (``ignore``), which it would otherwise read whole."""
        guard.after(done)
        ignore = (hits, walks, ignored, *stores)
        ignored.append(all(any(s is i for i in ignore) for s in stores))
        seen = reachable([*markers[:done], canary], ignore=ignore)
        walks.append(any(hit.call == done for hit in seen))
        hits.extend(
            {"call": hit.call + 1, "after": done, "kind": hit.kind, "chain": list(hit.chain)}
            for hit in seen
            if hit.call != done
        )

    with tempfile.TemporaryDirectory() as tmp:
        outcomes = run(Path(tmp), markers, after)
    CANARY.clear()
    pack = importlib.import_module("aibi.packs.onco").__file__
    report = {
        "pid": os.getpid(),
        "fresh_start": fresh_start,
        "pack": str(Path(str(pack)).parent),
        "outcomes": outcomes,
        "walks": len(walks),
        "canary_seen": walks != [] and all(walks),
        "hits": hits,
        "stores_ignored": ignored != [] and all(ignored),
        **guard.report(),
    }
    print(json.dumps(report))


def report_of(output: str) -> Mapping[str, Any]:
    """The report in the child's output (its last line)."""
    found = json.loads(output.strip().splitlines()[-1])
    assert isinstance(found, dict)
    return found


if __name__ == "__main__":
    main()

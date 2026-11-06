"""The retention guards over the pack's own state (issue #136, PR B; D438): an **allow-list** of
what every piece of it may be, by type identity, and a **snapshot** of its content, compared after
every call with the one taken before the first.

The marker walk (``retention_scan.reachable``) finds a value a call kept whole. A value kept
derived (a prefix shorter than a marker, a digest, a count) carries no marker, and the walk cannot
see it; these two guards hold the pack's own state whatever it holds:

- ``allowed`` judges every object bound in the pack's own modules (``PREFIX``): each module's
  values, each class's attributes, each function's defaults, keyword defaults, closure cells and
  ``__dict__``, and the instances they hold, by **a closed set of types, told by identity**
  (``RULES``, one row for each kind of thing it admits, with its reason; ``EXEMPT``, the objects it
  admits by identity, which it looks into all the same). A callable is admitted only with its
  ``__self__`` judged (``[].append`` is a list's). Of another package's machinery reached inside
  a foreign holder (an exempt object or a frozen model of another package, a typing object), the
  types of ``stop_set`` are admitted, and only their type: everything one holds is judged by the
  rules, through plain containers; a pack-owned object is judged in full whatever path reached it
  (the context is reset on entering one, and an object is judged once in each context). A module
  is admitted only as ``sys.modules``' own. Module and class dunders are admitted by a closed list
  of names (``MODULE_MACHINERY``, ``CLASS_MACHINERY``), never by a pattern, each in its shape and
  what it holds judged; every rule looks into what it admits, or is a leaf ``LEAVES`` names.
- ``snapshot`` is a digest of the content of the same state and more: the pack's modules, every
  class and function of ``aibi`` (the core's too: a value set on a core class or function the
  pack imports is the pack's state) and every instance, container and cell they hold, entered
  through their base types' own reads (``gc.get_referents``, the C getsets), and a module not
  registered and a class its module does not bind; it stops at another package's registered
  modules, bound classes and functions, at code objects and at ``abc``'s caches. It holds
  **digests only** (the interpreter's keyed hash of each node's text), keyed by paths whose
  labels are a dict key's text only where that text is a name written in the pack's source
  (``names_of``) or the interpreter's, else the key's keyed digest, so that no snapshot holds a
  value, and
  reads **content, not representation**: an instance's ``__dict__`` is materialised through its
  class's C getset and a function's annotations and ``__dict__`` through theirs (a third party's
  ``vars()`` or ``inspect.signature`` changes nothing), a class's ``__slotnames__`` that
  ``copyreg`` writes (``copy``, ``deepcopy``, ``pickle``) reads as absent when it is what
  ``copyreg`` computes, and every object it enters is kept alive for the pass, so that an id it
  has seen is never another object's (a class's dict is read behind its mappingproxy, so that the
  pass makes no temporary of its own).

``Guard`` is what the scan's child runs: the allow-list and the first snapshot once the pack is
imported, before the first call, and both after every call, each snapshot compared with the
first. Both run no code of what they read, by the walk's rule: a type is told by its identity and
by C-level subtype checks (never ``isinstance``, ``in`` or ``==`` against a type), a class's names
by iterating its own dict (a lookup would hash and compare a colliding key), an attribute through
the base type's own descriptor, an instance's dict through the getset its class's MRO holds, which
is checked to be one. Their reports give paths and types alone, never a value.
"""

import dataclasses
import gc
import hashlib
import io
import re
import secrets
import sys
import tokenize
import typing
from array import array
from collections import namedtuple
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from types import (
    BuiltinFunctionType,
    CellType,
    CodeType,
    FunctionType,
    GenericAlias,
    GetSetDescriptorType,
    MappingProxyType,
    MemberDescriptorType,
    MethodType,
    MethodWrapperType,
    ModuleType,
    UnionType,
    WrapperDescriptorType,
)
from typing import Any, NamedTuple


def _loaders() -> tuple[tuple[object, ...], tuple[type, ...]]:
    import importlib.machinery
    import zipimport

    classes = (importlib.machinery.BuiltinImporter, importlib.machinery.FrozenImporter)
    types = (
        importlib.machinery.SourceFileLoader, importlib.machinery.SourcelessFileLoader,
        importlib.machinery.ExtensionFileLoader, importlib.machinery.NamespaceLoader,
        zipimport.zipimporter,
    )  # fmt: skip
    return classes, types


_LOADER_CLASSES, _LOADER_TYPES = _loaders()
"""The import system's loaders: the classes that load builtin and frozen modules themselves, and
the exact types of the others."""
_ALIAS = getattr(typing, "TypeAliasType", None)
"""The type of a ``type X = ...`` alias, whose value is computed and cached when first read."""
_ModuleSpec = type(sys.__spec__)
_SPEC_DICT = _ModuleSpec.__dict__["__dict__"]

PREFIX = "aibi.packs.onco"
"""The pack's own modules: what the allow-list judges, and what owns a class or an instance."""
CORE = "aibi"
"""The modules whose classes and functions the snapshot enters (the core's and the pack's)."""

_TYPE_DICT: Callable[[type], MappingProxyType[str, Any]] = type.__dict__["__dict__"].__get__
_TYPE_MRO: Callable[[type], tuple[type, ...]] = type.__dict__["__mro__"].__get__
_TYPE_QUALNAME = type.__dict__["__qualname__"].__get__
_TYPE_NAME = type.__dict__["__name__"].__get__
_TYPE_MODULE = type.__dict__["__module__"].__get__
_TYPE_FLAGS: Callable[[type], int] = type.__dict__["__flags__"].__get__
_HEAPTYPE = 1 << 9
_FUNCTION = FunctionType.__dict__
_FUNC_MODULE = _FUNCTION["__module__"].__get__
_FUNC_QUALNAME = _FUNCTION["__qualname__"].__get__
_FUNC_DEFAULTS = _FUNCTION["__defaults__"].__get__
_FUNC_KWDEFAULTS = _FUNCTION["__kwdefaults__"].__get__
_FUNC_CLOSURE = _FUNCTION["__closure__"].__get__
_FUNC_CODE = _FUNCTION["__code__"].__get__
_FUNC_GLOBALS = _FUNCTION["__globals__"].__get__
_FUNC_ANNOTATIONS = _FUNCTION["__annotations__"].__get__
_FUNC_DICT = _FUNCTION["__dict__"].__get__
_FUNC_BUILTINS = _FUNCTION["__builtins__"].__get__
_CODE_FILENAME = CodeType.__dict__["co_filename"].__get__
_MODULE_DICT: Callable[[ModuleType], dict[str, Any]] = vars(ModuleType)["__dict__"].__get__
_BUILTIN_SELF = BuiltinFunctionType.__dict__["__self__"].__get__
_WRAPPER_SELF = MethodWrapperType.__dict__["__self__"].__get__
_METHOD_SELF = MethodType.__dict__["__self__"].__get__
_METHOD_FUNC = MethodType.__dict__["__func__"].__get__
_CELL = CellType.__dict__["cell_contents"].__get__
_GETSET_GET = GetSetDescriptorType.__get__
_MEMBER_GET = MemberDescriptorType.__get__
_PARAMS = dataclasses._DataclassParams  # pyright: ignore[reportAttributeAccessIssue]
_PARAMS_FROZEN = _PARAMS.__dict__["frozen"].__get__
_TUPLEGETTER = type(vars(namedtuple("_Pair", "a"))["a"])
_SUBCLASS = type.__subclasscheck__
_DICT_ITEMS: Callable[[Any], Iterable[tuple[object, object]]] = dict.items
_DICT_LEN: Callable[[Any], int] = dict.__len__
_LIST_COPY: Callable[[Any, slice], list[object]] = list.__getitem__
_TUPLE_COPY: Callable[[Any, slice], tuple[object, ...]] = tuple.__getitem__
_STR_COPY: Callable[[Any, slice], str] = str.__getitem__
_BYTES_COPY: Callable[[Any, slice], bytes] = bytes.__getitem__
_BYTEARRAY_HEX: Callable[[Any], str] = bytearray.hex
_ARRAY_TOBYTES: Callable[[Any], bytes] = array.tobytes
_ARRAY_TYPECODE: Callable[[Any], str] = array.__dict__["typecode"].__get__
_MEMORYVIEW_TOBYTES: Callable[[Any], bytes] = memoryview.tobytes
_STRINGIO_GETVALUE: Callable[[Any], str] = io.StringIO.getvalue
_BYTESIO_GETVALUE: Callable[[Any], bytes] = io.BytesIO.getvalue
_INT_INDEX: Callable[[Any], int] = int.__index__
_FLOAT_HEX: Callable[[Any], str] = float.hex
_COMPLEX_PARTS = (complex.__dict__["real"].__get__, complex.__dict__["imag"].__get__)
_ALL = slice(None)
_MISSING = object()

ATOMS = frozenset(
    id(kind)
    for kind in (str, bytes, int, float, bool, type(None), complex, type(...), type(NotImplemented))
)
"""The ids of the exact types that hold nothing and cannot change: never the types, which a set
would hash and compare, and whose metaclass may run code for either."""


def _ids(kinds: Iterable[type]) -> frozenset[int]:
    return frozenset(id(kind) for kind in kinds)


def _is_class(kind: type) -> bool:
    """Whether ``kind`` (a type) is a metaclass: its objects are classes (C-level, no code)."""
    return _SUBCLASS(type, kind)


def _sub(kind: type, base: type) -> bool:
    """``kind`` is ``base`` or a subclass of it, by the C-level check (no metaclass runs)."""
    return _SUBCLASS(base, kind)


def _own(name: object) -> bool:
    """Whether ``name`` (a module's name) is the pack's or one of its modules'."""
    return type(name) is str and (name == PREFIX or str.startswith(name, PREFIX + "."))


def _core(name: object) -> bool:
    """Whether ``name`` (a module's name) is ``aibi``'s or one of its modules'."""
    return type(name) is str and (name == CORE or str.startswith(name, CORE + "."))


def class_dict(kind: type) -> dict[str, Any]:
    """A class's own dict, the one behind its mappingproxy (``gc.get_referents``: no code)."""
    [inner] = gc.get_referents(_TYPE_DICT(kind))
    return inner


def _lookup(mapping: dict[Any, Any], name: str) -> object:
    """``mapping``'s value under the exact ``str`` key ``name``, found by iterating it and
    comparing the keys that are exactly ``str``: a lookup would hash and compare a colliding key
    of another type."""
    for key, value in _DICT_ITEMS(mapping):
        if type(key) is str and str.__eq__(key, name):
            return value
    return _MISSING


_KEY = secrets.token_bytes(16)
"""The digests' key, the process's own: snapshots are compared within one process alone, and a
digest of a short value cannot be brute-forced from a report."""


def _digest(text: str) -> str:
    encoded = text.encode("utf-8", "surrogatepass")
    return hashlib.blake2b(encoded, digest_size=8, key=_KEY).hexdigest()


def label(key: str, names: frozenset[str]) -> str:
    """A dict key's label in a path: its text where it is a name written in the pack's source
    (``names_of``: code, never a value), else ``#`` and its digest, so that a path never holds a
    key's text that a call could have given."""
    return key if key in names or key in _CODE_NAMES else f"#{_digest(key)}"


_CODE_NAMES = frozenset(
    {"__name__", "__doc__", "__package__", "__loader__", "__spec__", "__file__", "__cached__"}
    | {"__builtins__", "__path__", "__annotations__", "__all__", "__module__", "__qualname__"}
    | {"__dict__", "__weakref__", "__dataclass_params__", "__dataclass_fields__", "_fields"}
    | {"__match_args__", "__slots__", "_field_defaults", "__orig_bases__", "__hash__"}
    | {"__parameters__", "__type_params__", "__slotnames__", "_abc_impl", "__init__"}
)
"""The interpreter's and the libraries' names, shown as they are in a path."""


def names_of(sources: Iterable[str]) -> frozenset[str]:
    """The names written in ``sources``: every name token, and every string literal that is an
    identifier (a key written as a ``str``)."""
    found: set[str] = set()
    for source in sources:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type == tokenize.NAME:
                found.add(token.string)
            elif token.type == tokenize.STRING:
                text = token.string.strip("rbuRBU")
                for quote in ('"""', "'''", '"', "'"):
                    if text.startswith(quote) and text.endswith(quote) and len(text) > 1:
                        inner = text[len(quote) : -len(quote)]
                        if inner.isidentifier():
                            found.add(inner)
                        break
    return frozenset(found)


def pack_names(modules: Sequence[ModuleType]) -> frozenset[str]:
    """``names_of`` the source files of ``modules``."""
    files = []
    for module in modules:
        file = _lookup(_MODULE_DICT(module), "__file__")
        if type(file) is str:
            files.append(Path(file).read_text(encoding="utf-8"))
    return names_of(files)


class _Kind(NamedTuple):
    """What a pass knows of a class: its module's name (``None`` where it is not an exact
    ``str``), a name for reports, the C getset of its instances' ``__dict__`` (``None`` where its
    MRO holds no getset there), and the rules that read its MRO."""

    module: object
    name: str
    own: bool
    core: bool
    getset: GetSetDescriptorType | None
    frozen_dataclass: bool
    namedtuple: bool
    frozen_model: bool
    model_slots: tuple[tuple[str, MemberDescriptorType], ...]


_MODEL_SLOTS = ("__pydantic_fields_set__", "__pydantic_extra__", "__pydantic_private__")
_MRO_NAMES = ("__dict__", "__dataclass_params__", "_fields", "model_config", *_MODEL_SLOTS)
_MRO_SET = frozenset(_MRO_NAMES)
_STATIC: dict[int, tuple[type, "_Kind"]] = {}
"""``_Kind`` of each static type met, for the process (the type kept alive beside it)."""


def _frozen_params(params: object) -> bool:
    """Whether ``params`` is an exact ``_DataclassParams`` whose ``frozen`` is ``True`` (one never
    initialised has no ``frozen``, and is not)."""
    if type(params) is not _PARAMS:
        return False
    try:
        return _PARAMS_FROZEN(params) is True
    except AttributeError:
        return False


class _Kinds:
    """``_Kind`` of each class met in a pass, by id, the class kept alive beside it (a class made
    at call time may be freed, and its id reused, between passes: so the record is a pass's)."""

    def __init__(self) -> None:
        self.known: dict[int, tuple[type, _Kind]] = {}

    def of(self, kind: type) -> _Kind:
        found = self.known.get(id(kind))
        if found is not None:
            return found[1]
        if _TYPE_FLAGS(kind) & _HEAPTYPE:
            module = _lookup(class_dict(kind), "__module__")
        else:
            module = _TYPE_MODULE(kind)  # a static type's, from its C name: no dict is read
        qualname = _TYPE_QUALNAME(kind)
        shown = qualname if type(qualname) is str else "?"
        name = f"{module}.{shown}" if type(module) is str else shown
        static = not _TYPE_FLAGS(kind) & _HEAPTYPE
        if static and (cached := _STATIC.get(id(kind))) is not None:
            self.known[id(kind)] = cached
            return cached[1]
        seen: dict[str, object] = {}
        for base in _TYPE_MRO(kind):  # each base's own dict read once, its exact str keys alone
            for key, value in _DICT_ITEMS(class_dict(base)):
                if type(key) is str and key in _MRO_SET and key not in seen:
                    seen[key] = value
        getset = seen.get("__dict__")
        params = seen.get("__dataclass_params__")
        config = seen.get("model_config")
        made = _Kind(
            module,
            name,
            _own(module),
            _core(module),
            getset if type(getset) is GetSetDescriptorType else None,
            _frozen_params(params),
            _sub(kind, tuple) and type(seen.get("_fields")) is tuple,
            type(config) is dict and _lookup(config, "frozen") is True,
            tuple(
                (name, held)
                for name in _MODEL_SLOTS
                if type(held := seen.get(name)) is MemberDescriptorType
            ),
        )
        self.known[id(kind)] = (kind, made)
        if static:  # a static type's dict is the interpreter's: no Python code sets it
            _STATIC[id(kind)] = (kind, made)
        return made

    def own_dict(self, item: object, kind: type) -> dict[str, Any] | None:
        """``item``'s own ``__dict__``, materialised through its class's C getset (content, not
        representation: an instance whose dict was never asked for reads as one whose dict is
        empty), or ``None`` where its class has none or defines ``__dict__`` otherwise (a
        property, which is never run)."""
        getset = self.of(kind).getset
        if getset is None:
            return None
        try:
            found = _GETSET_GET(getset, item, kind)
        except (AttributeError, TypeError):
            return None
        return found if type(found) is dict else None


def slotnames(kind: type) -> list[str]:
    """What ``copyreg._slotnames`` computes for ``kind`` (and writes into its dict as
    ``__slotnames__``), read from the MRO's own dicts: each ``__slots__`` name but ``__dict__``
    and ``__weakref__``, a private one mangled."""
    names: list[str] = []
    for base in _TYPE_MRO(kind):
        slots = _lookup(class_dict(base), "__slots__")
        if slots is _MISSING:
            continue
        if type(slots) is str:
            slots = (slots,)
        if type(slots) is not tuple and type(slots) is not list:
            continue
        name = _TYPE_NAME(base)
        for slot in slots:
            if type(slot) is not str or slot in ("__dict__", "__weakref__"):
                continue
            if slot.startswith("__") and not slot.endswith("__") and type(name) is str:
                stripped = name.lstrip("_")
                if stripped:
                    slot = f"_{stripped}{slot}"
            names.append(slot)
    return names


def _slotnames_kept(kind: type, value: object) -> bool:
    """Whether a class's ``__slotnames__`` is what ``copyreg`` writes there: an exact list of
    exact ``str``, the names ``slotnames`` computes."""
    if type(value) is not list:
        return False
    items = _LIST_COPY(value, _ALL)
    return all(type(item) is str for item in items) and items == slotnames(kind)


# --- The allow-list ------------------------------------------------------------------------------


class Rule(NamedTuple):
    """One kind of thing the allow-list admits: what, why it holds no state a call writes, and
    each part of it that is judged in turn (``reads``, its legs), or none for a leaf, which
    ``LEAVES`` must name. Each rule's admission and each leg has a negative twin
    (``retention_state_pins.RULE_TWINS``)."""

    name: str
    admits: str
    reason: str
    reads: tuple[str, ...]


RULES = (
    Rule("atom", "an exact str, bytes, int, float, bool, None, complex, Ellipsis or NotImplemented",
         "it holds nothing and cannot change", ()),
    Rule("tuple or frozenset", "an exact tuple or frozenset", "it cannot change",
         ("a tuple's items", "a frozenset's members")),
    Rule("pattern", "an exact re.Pattern", "a compiled constant (re's own cache is the standard "
         "library's state, D438)", ()),
    Rule("library module", "a module the import system made outside aibi: sys.modules' own under "
         "its exact str __name__, its __spec__ an exact ModuleSpec of that name, its __loader__ "
         "the import system's", "another library's state is its library's (D438 names an "
         "attribute set on one as unheld)", ()),
    Rule("core module", "a module of aibi's but the pack's the import system made (as a library "
         "module)", "the snapshot enters it, as it does the core's classes and functions", ()),
    Rule("pack module", "a root of the pass, or one of the pack's modules the import system made "
         "(as a library module)", "it is judged as a root of its own", ()),
    Rule("class", "a class", "a class of another package is that package's state",
         ("a pack class's own dict's values", "a pack class's own dict's keys",
          "a pack class's name and qualified name")),
    Rule("function", "a Python function", "its code is code; another package's function (its "
         "__globals__ a registered module's dict) is that package's state",
         ("its defaults", "its keyword defaults", "its closure cells", "its __dict__",
          "its names, module, doc, annotations and type parameters")),
    Rule("descriptor", "an exact staticmethod, classmethod or property", "a wrapper of functions",
         ("each function", "the wrapper's own dict", "the wrapper's __annotations__")),
    Rule("slot descriptor", "in a class's own dict, an exact getset, member or wrapper "
         "descriptor, or a NamedTuple field's getter", "the class's machinery, made by the "
         "interpreter: it holds no value", ()),
    Rule("unbound builtin", "a builtin function or method-wrapper whose __self__ is None or a "
         "module another rule admits", "bound to nothing a call writes", ()),
    Rule("bound callable", "a builtin, method-wrapper or bound method whose __self__ would "
         "itself be admitted", "a callable bound to a list ([].append) keeps what it is given",
         ("its __self__", "its function")),
    Rule("typing", "an object of one of TYPING's exact types (a type alias inside a foreign holder "
         "alone: the pack's own is refused, as its cached value is held by no guard)",
         "a type hint, made at import",
         ("everything it refers to, inside a foreign holder",)),
    Rule("mappingproxy", "an exact MappingProxyType over an exact dict", "read-only: the dict "
         "behind it is reached through it alone", ("its keys", "its values")),
    Rule("pack NamedTuple", "an instance of a tuple subclass of the pack's whose MRO holds an "
         "exact tuple _fields", "it cannot change; a tuple subclass that says it is one by a "
         "_fields of its own is harmless, as all it holds is judged", ("its items",
         "its own dict")),
    Rule("pack frozen dataclass", "an instance of a class of the pack's whose MRO holds an exact "
         "_DataclassParams with frozen True", "its fields cannot be set (a dict field is the "
         "owner's, read by its slot)", ("its own dict",)),
    Rule("frozen pydantic model", "an instance of a class whose MRO holds an exact dict "
         "model_config with frozen True", "its fields cannot be set", ("its fields",
         "its fields set", "its extra", "its private dict")),
    Rule("exempt", "an object EXEMPT names, by identity", "an object the pack hands the core, of "
         "a class that is neither frozen nor the pack's", ("all it holds, as a frozen model's",)),
    Rule("JSON container", "an exact dict or list held by an exempt object, a frozen pydantic "
         "model or an exempt mappingproxy (a JSON value)", "JSON a model or a schema holds as "
         "data: the snapshot holds its content", ("its keys", "its items")),
    Rule("fields set", "an exact set of exact str in a pydantic model's slot "
         "(__pydantic_fields_set__)", "the names of the fields given, made by pydantic",
         ("each member, an exact str",)),
    Rule("stop set", "inside a foreign holder, an object of one of stop_set's exact types",
         "another package's machinery, whose own type no rule names: its type alone is waived",
         ("everything it holds, through plain containers and other stop-set objects",
          "as a callable's __self__")),
    Rule("module machinery", "a module's __loader__, __spec__ or __builtins__ of the import "
         "system's exact types, a __path__ of str, an __annotations__ dict", "the import "
         "system's", ("the shape of __loader__, __spec__ and __builtins__",
         "each item of __path__", "the annotations' keys and values")),
    Rule("__all__", "a module's __all__, an exact list of exact str", "the module's names",
         ("each item, an exact str",)),
    Rule("class machinery", "a class's __annotations__, __dataclass_fields__, "
         "__dataclass_params__, _field_defaults, _fields or _abc_impl, each of its shape",
         "the class's own machinery, made by the interpreter, dataclasses, typing or abc",
         ("__annotations__", "__dataclass_fields__", "__dataclass_params__", "_field_defaults",
          "_fields", "_abc_impl")),
    Rule("__slotnames__", "a class's __slotnames__: an exact list of exact str, the names "
         "copyreg computes from the MRO's __slots__", "copy, deepcopy and pickle of an instance "
         "write it (copyreg)", ("each item, against what copyreg computes",)),
    Rule("repr_running", "an empty exact set in a closure cell of a dataclass's __repr__ (its "
         "code dataclasses')", "dataclasses' recursion guard, empty after any call",
         ("that it is empty",)),
)  # fmt: skip
"""Every kind of thing the allow-list admits: each admission is counted by its rule's name, and
on the clean pack every rule admits something and nothing is refused (the suite asserts both);
each rule's admission and each of its legs has a negative twin it refuses
(``retention_state_pins.RULE_TWINS``)."""
LEAVES = {
    "atom": "holds no object",
    "pattern": "its pattern and flags are atoms; its group index is the compiler's",
    "library module": "the import system made it outside aibi: its state is its library's, an "
    "attribute set on one named as unheld",
    "core module": "the snapshot enters it: a value a call sets on it is seen",
    "pack module": "a root of the pass, or one of the pack's modules, each judged as a root",
    "slot descriptor": "the interpreter's, holding a name and a slot",
    "unbound builtin": "its __self__ is None or a module another rule admits",
}
"""The rules that admit without judging what the object holds, each with why it holds nothing a
call writes, or why what it holds is held elsewhere
(``test_every_rule_looks_into_what_it_admits_or_is_a_named_leaf``)."""
MODULE_MACHINERY = frozenset({"__loader__", "__spec__", "__builtins__", "__path__"})
"""The module dunders the import system writes that no other rule admits, by name (each checked
for its shape); every other dunder (``__name__``, ``__doc__``, ``__file__``...) is judged as a
value, and ``__annotations__`` by its keys and values."""
CLASS_MACHINERY = frozenset(
    {"__annotations__", "__dataclass_fields__", "__dataclass_params__", "_field_defaults"}
    | {"_fields", "_abc_impl"}
)
"""A class's own names that the interpreter, ``dataclasses``, ``typing`` and ``abc`` write with a
shape no other rule admits, by name, each checked for its shape and what it holds judged; every
other name (``__module__``, ``__hash__``, ``__orig_bases__``, ``__slots__``...) is judged as a
value (``__firstlineno__`` and ``__static_attributes__``, 3.13's, are atoms and tuples)."""


def _typing() -> tuple[type, ...]:
    import collections.abc

    import typing_extensions

    named = [
        (typing, "_GenericAlias"), (typing, "_LiteralGenericAlias"), (typing, "_AnnotatedAlias"),
        (typing, "_UnionGenericAlias"), (typing, "_SpecialForm"), (typing, "_LiteralSpecialForm"),
        (typing, "_TypedCacheSpecialForm"), (typing, "TypeAliasType"), (typing, "NewType"),
        (typing, "TypeVar"), (collections.abc, "_CallableGenericAlias"),
        (typing_extensions, "TypeAliasType"),
    ]  # fmt: skip
    found: list[Any] = [GenericAlias, UnionType]
    for module, name in named:
        kind = vars(module).get(name)
        if kind is not None and all(kind is not known for known in found):
            found.append(kind)
    return tuple(found)


TYPING = _typing()
"""The exact types of the typing objects the pack's modules bind (``_TypedCacheSpecialForm`` is
3.13's ``Literal``)."""


def stop_set() -> tuple[tuple[type, str], ...]:
    """The closed set of another package's machinery whose type no rule admits, met inside a
    foreign holder alone (never bound in a pack module, nor reached through a pack-owned object),
    each with why: the allow-list waives its type alone, and judges everything it holds by the
    rules, through plain containers and other members (imported when a guard is made, after the
    pack)."""
    import functools

    import pydantic
    import pydantic.functional_validators
    import pydantic.json_schema
    import pydantic_core

    from aibi.core.schema import output

    return (
        (MethodType, "the bound classmethods in a TypeAdapter's core schema"),
        (functools.partial, "a partial in a TypeAdapter's core schema"),
        (pydantic.Tag, "the Annotated metadata of the core's tagged unions"),
        (pydantic.Discriminator, "the Annotated metadata of the core's tagged unions"),
        (pydantic.functional_validators.PlainValidator, "the core's JsonValue's metadata"),
        (typing.cast(type, pydantic.json_schema.SkipJsonSchema), "the core's Annotated metadata"),
        (pydantic.json_schema.WithJsonSchema, "the core's Annotated metadata (a schema, JSON)"),
        (output._AnyOf, "the Annotated metadata of the core's Segment"),  # pyright: ignore[reportPrivateUsage]
        (pydantic_core.SchemaValidator, "a TypeAdapter's validator, opaque, built at import"),
        (pydantic_core.SchemaSerializer, "a TypeAdapter's serialiser, opaque, built at import"),
    )


def _machinery_types() -> dict[str, tuple[object, ...]]:
    """The exact types (and objects) the import system's and dataclasses' machinery may be."""
    import abc
    import builtins
    import importlib.machinery

    loaders = (
        importlib.machinery.SourceFileLoader, importlib.machinery.SourcelessFileLoader,
        importlib.machinery.ExtensionFileLoader,
    )  # fmt: skip
    return {
        "__loader__": loaders,
        "__spec__": (importlib.machinery.ModuleSpec,),
        "__builtins__": (vars(builtins),),
        "_abc_impl": (type(vars(abc.ABC)["_abc_impl"]),),
        "fields": (dataclasses.Field,),
        "sentinels": (
            dataclasses.MISSING,
            dataclasses._FIELD,  # pyright: ignore[reportAttributeAccessIssue]
            dataclasses._FIELD_CLASSVAR,  # pyright: ignore[reportAttributeAccessIssue]
            dataclasses._FIELD_INITVAR,  # pyright: ignore[reportAttributeAccessIssue]
        ),
    }


EXEMPT = (
    ("aibi.packs.onco", "PACK", "the core's frozen Pack the registry reads"),
    ("aibi.packs.onco", "PACK.importer", "the pack's importer, of a plain class"),
    ("aibi.packs.onco", "PACK.validator", "the pack's validator, of a plain class"),
    ("aibi.packs.onco.build", "_ADAPTER", "pydantic's TypeAdapter of the core's Descriptor"),
    ("aibi.packs.onco.schemas", "SCHEMAS", "the extension schemas: JSON under a mappingproxy"),
    ("aibi.packs.onco.schemas", "DATASET", "the dataset extension's schema, JSON"),
    ("aibi.packs.onco.schemas", "TABLE", "the table extension's schema, JSON"),
    ("aibi.packs.onco.schemas", "COLUMN", "the column extension's schema, JSON"),
)
"""The objects the allow-list admits by identity (module, dotted name, why), each looked into."""


def exemptions(modules: Sequence[ModuleType]) -> dict[int, object]:
    """``EXEMPT``'s objects, by id (each kept alive beside it), read through module dicts and
    instances' own dicts (no attribute lookup runs code); a name not found fails."""
    by_name = {_lookup(_MODULE_DICT(m), "__name__"): m for m in modules}
    kinds = _Kinds()
    found: dict[int, object] = {}
    for module, dotted, _ in EXEMPT:
        first, *rest = dotted.split(".")
        value = _lookup(_MODULE_DICT(by_name[module]), first)
        for name in rest:
            own = kinds.own_dict(value, type(value))
            value = _MISSING if own is None else _lookup(own, name)
        assert value is not _MISSING, f"{module}:{dotted} is not bound"
        found[id(value)] = value
    return found


class Registry:
    """What ``sys.modules`` holds, read once for a pass by iterating it: each module registered
    under its own exact ``str`` name, by id, and the ids of the pack's, ``aibi``'s and every other
    registered module's dicts (a function is another package's only when its ``__globals__`` is
    one of the last: identity, never the ``__module__`` it says)."""

    def __init__(self) -> None:
        modules = sys.modules
        if type(modules) is not dict:
            raise RuntimeError("sys.modules is not a dict: the guards cannot read it")
        self.registered: dict[int, ModuleType] = {}
        self.by_name: dict[str, ModuleType] = {}
        self.pack: set[int] = set()
        self.core: set[int] = set()
        self.foreign: set[int] = set()
        self.bound: dict[int, tuple[type, bool]] = {}
        self.names: dict[str, dict[str, object]] = {}
        for key, module in _DICT_ITEMS(modules):
            if type(key) is not str or type(module) is not ModuleType:
                continue
            name = _lookup(_MODULE_DICT(module), "__name__")
            if type(name) is not str or not str.__eq__(name, key):
                continue
            self.registered[id(module)] = module
            self.by_name[key] = module
            if _own(key):
                self.pack.add(id(_MODULE_DICT(module)))
            else:
                self.foreign.add(id(_MODULE_DICT(module)))
            if _core(key):
                self.core.add(id(_MODULE_DICT(module)))

    def own(self, function: FunctionType) -> bool:
        """A function the allow-list judges in full: one whose ``__globals__`` is not another
        registered module's dict (a pack module's, or a namespace made for it: a NamedTuple's
        ``__new__``, a function made by ``FunctionType``), or whose ``__module__`` is the pack's
        (a library's wrapper of a pack function says so)."""
        return id(_FUNC_GLOBALS(function)) not in self.foreign or _own(_FUNC_MODULE(function))

    def of_core(self, function: FunctionType) -> bool:
        return id(_FUNC_GLOBALS(function)) in self.core or _core(_FUNC_MODULE(function))

    def module_kind(self, module: ModuleType, roots: frozenset[int]) -> str | None:
        """What a module is to the guards: ``pack module`` (a root of the pass, or one of the
        pack's the import system made), ``core module`` (``aibi``'s, the import system's: the
        snapshot enters it), ``library module`` (the import system made it outside ``aibi``), or
        ``None``: a module the pack made, registered or not, under any name (its own prefix
        among them), which the allow-list refuses and the snapshot enters. The import system made
        it when it is ``sys.modules``' own under its exact ``str`` name, its ``__spec__`` an exact
        ``ModuleSpec`` of that name and its ``__loader__`` the import system's."""
        if id(module) in roots:
            return "pack module"
        if id(module) not in self.registered:
            return None
        found = _MODULE_DICT(module)
        name = _lookup(found, "__name__")
        spec = _lookup(found, "__spec__")
        if type(spec) is not _ModuleSpec:
            return None
        try:
            fields = _GETSET_GET(_SPEC_DICT, spec, _ModuleSpec)
        except (AttributeError, TypeError):
            return None
        spec_name = _lookup(fields, "name") if type(fields) is dict else None
        if not (type(spec_name) is str and str.__eq__(spec_name, name)):  # pyright: ignore[reportArgumentType]
            return None
        loader = _lookup(found, "__loader__")
        if not any(loader is known for known in _LOADER_CLASSES) and not any(
            type(loader) is known for known in _LOADER_TYPES
        ):
            return None
        if _own(name):
            return "pack module"
        return "core module" if _core(name) else "library module"

    def binds(self, kind: type) -> bool:
        """Whether ``kind``'s module, registered, binds it under its qualified name (a nested
        name through the classes it names), or ``kind`` is a static type (C's: never made by a
        pack); a class made at call time (``<locals>``) is bound nowhere."""
        known = self.bound.get(id(kind))
        if known is not None:
            return known[1]
        found = self._binds(kind)
        self.bound[id(kind)] = (kind, found)
        return found

    def _binds(self, kind: type) -> bool:
        if not _TYPE_FLAGS(kind) & _HEAPTYPE:
            return True
        module = _lookup(class_dict(kind), "__module__")
        qualname = _TYPE_QUALNAME(kind)
        if type(module) is not str or type(qualname) is not str:
            return False
        place: object = self.by_name.get(module)
        if place is None:
            return False
        names = self.names.get(module)
        if names is None:  # the module's names: its exact str keys alone, hashed as str
            names = {k: v for k, v in _DICT_ITEMS(_MODULE_DICT(place)) if type(k) is str}  # pyright: ignore[reportArgumentType]
            self.names[module] = names
        first, _, rest = qualname.partition(".")
        held: object = names.get(first, _MISSING)
        for part in rest.split(".") if rest else ():
            if held is not _MISSING and type(held) is not dict:
                held = class_dict(held) if _is_class(type(held)) else _MISSING  # pyright: ignore[reportArgumentType]
            if held is _MISSING:
                return False
            held = _lookup(held, part)  # pyright: ignore[reportArgumentType]
        return held is kind

    def shown(self, facts: "_Kind", kind: type, names: frozenset[str]) -> str:
        """A class's name for a report: ``module.qualname`` where the module is registered and
        the qualname is a name of the pack's source or binds that very class in its module (a
        library's class), else the module and ``#`` and a digest: a class made at call time and
        named by a value never prints the value."""
        module = facts.module
        qualname = _TYPE_QUALNAME(kind)
        if type(module) is not str or type(qualname) is not str:
            return "#" + _digest(repr(id(kind)))
        if not _TYPE_FLAGS(kind) & _HEAPTYPE:  # a static type: named by C code, never a value
            return f"{module}.{qualname}"
        place = self.by_name.get(module)
        if place is None:
            return "#" + _digest(module)
        if qualname in names or self.binds(kind):
            return f"{module}.{qualname}"
        return f"{module}.#{_digest(qualname)}"


class Reject(NamedTuple):
    """What the allow-list refused: where (a path of names and labels) and what it is (a type's
    name where it is a library's or the pack's source's, else its digest)."""

    path: str
    kind: str


class Judge:
    """One pass of the allow-list over the pack's modules (``allowed``)."""

    def __init__(
        self, exempt: Mapping[int, object], names: frozenset[str], roots: frozenset[int]
    ) -> None:
        self.exempt = exempt
        self.names = names
        self.roots = roots
        self.kinds = _Kinds()
        self.registry = Registry()
        self.machinery = _machinery_types()
        self.stop = _ids(kind for kind, _ in stop_set())
        self.typing = _ids(TYPING)
        self.seen: dict[tuple[int, bool], object] = {}
        self.through: dict[int, object] = {}
        self.rooted: set[int] = set()
        self.rejects: list[Reject] = []
        self.admitted: dict[str, int] = {}
        self.stopped: dict[str, int] = {}

    def admit(self, rule: str) -> None:
        self.admitted[rule] = self.admitted.get(rule, 0) + 1

    def name(self, kind: type) -> str:
        return self.registry.shown(self.kinds.of(kind), kind, self.names)

    def reject(self, path: str, kind: str) -> None:
        self.rejects.append(Reject(path, kind))

    def refuse(self, path: str, item: object) -> None:
        self.reject(path, self.name(type(item)))

    def key(self, key: object, index: int) -> str:
        return label(key, self.names) if type(key) is str else f"#{index}"

    def items(self, mapping: object, path: str, inside: bool, json: bool = False) -> None:
        """A dict's keys (any not an atom judged) and values, each by the rules; ``json``: a
        dict or list value is a JSON container."""
        for index, (key, value) in enumerate(_DICT_ITEMS(mapping)):
            if id(type(key)) not in ATOMS:
                self.judge(key, f"{path}[key {index}]", inside)
            at = f"{path}[{self.key(key, index)}]" if json else f"{path}.{self.key(key, index)}"
            if json and (type(value) is dict or type(value) is list):
                self.json(value, at, inside)  # pyright: ignore[reportArgumentType]
            else:
                self.judge(value, at, inside)

    def module(self, module: ModuleType, name: str) -> None:
        """A module of the pack: each value it binds (and any key not an atom), the import
        system's machinery by its shape."""
        for index, (key, value) in enumerate(_DICT_ITEMS(_MODULE_DICT(module))):
            path = f"{name}:{self.key(key, index)}"
            if id(type(key)) not in ATOMS:
                self.judge(key, f"{name}:[key {index}]")
            if type(key) is str and key in MODULE_MACHINERY:
                self.module_machinery(key, value, path)
            elif type(key) is str and key == "__all__":
                if type(value) is list and all(type(i) is str for i in _LIST_COPY(value, _ALL)):
                    self.admit("__all__")
                else:
                    self.reject(path, "a module's __all__ that is not a list of str")
            elif type(key) is str and key == "__annotations__" and type(value) is dict:
                self.admit("module machinery")
                self.items(value, path, True)
            else:
                self.judge(value, path)

    def module_machinery(self, key: str, value: object, path: str) -> None:
        if value is None and key in ("__loader__", "__spec__"):  # a module made, not imported
            ok = True
        elif key == "__path__":
            ok = type(value) is list and all(type(i) is str for i in _LIST_COPY(value, _ALL))
        elif key == "__builtins__":
            ok = any(value is known for known in self.machinery[key])
        else:
            ok = any(type(value) is known for known in self.machinery[key])
        if ok:
            self.admit("module machinery")
        else:
            self.reject(path, f"a module's {key} not of the import system's shape")

    def judge(self, item: object, path: str, inside: bool = False) -> None:
        """``item``, by the rules; ``inside`` a foreign holder, an object of the stop set is
        looked through (``look_through``). An object is judged once in each context."""
        kind = type(item)
        if id(kind) in ATOMS:
            self.admit("atom")
            return
        if (id(item), inside) in self.seen:
            return
        self.seen[(id(item), inside)] = item
        if kind is tuple or kind is frozenset:
            self.admit("tuple or frozenset")
            items = gc.get_referents(item) if kind is frozenset else _TUPLE_COPY(item, _ALL)
            for index, value in enumerate(items):
                self.judge(value, f"{path}[{index}]", inside)
        elif kind is FunctionType:
            self.admit("function")
            if self.registry.own(item):  # pyright: ignore[reportArgumentType]
                self.function(item, path)  # pyright: ignore[reportArgumentType]
        elif kind is staticmethod or kind is classmethod or kind is property:
            self.admit("descriptor")
            for index, value in enumerate(self.referents(item, kind)):
                if type(value) is dict:  # a static or class method's own dict (its wrapper's names)
                    for at, (key, held) in enumerate(_DICT_ITEMS(value)):
                        if type(key) is str and key == "__annotations__" and type(held) is dict:
                            self.items(held, f"{path}.__annotations__", True)
                        else:
                            self.judge(held, f"{path}.__dict__[{self.key(key, at)}]", inside)
                else:
                    self.judge(value, f"{path}<{index}>", inside)
        elif _is_class(kind):
            self.admit("class")
            if self.owned(item):  # pyright: ignore[reportArgumentType]
                self.klass(item, path)  # pyright: ignore[reportArgumentType]
        elif kind is ModuleType:
            found = self.registry.module_kind(item, self.roots)  # pyright: ignore[reportArgumentType]
            if found is None:
                self.reject(path, "a module the pack made (no rule admits it)")
            else:
                self.admit(found)
                if found == "pack module" and id(item) not in self.roots:  # judged as a root
                    self.module(item, _module_label(item))  # pyright: ignore[reportArgumentType]
        elif kind is re.Pattern:
            self.admit("pattern")
        elif kind is BuiltinFunctionType or kind is MethodWrapperType:
            bound = _BUILTIN_SELF(item) if kind is BuiltinFunctionType else _WRAPPER_SELF(item)
            if bound is None or (
                type(bound) is ModuleType
                and self.registry.module_kind(bound, self.roots) is not None  # pyright: ignore[reportArgumentType]
            ):
                self.admit("unbound builtin")
            else:
                self.bound(bound, f"{path}.__self__", inside)
        elif inside and id(kind) in self.stop:
            self.stopped_by(kind)
            self.look_through(item, path)
        elif kind is MethodType:
            self.bound(_METHOD_SELF(item), f"{path}.__self__", inside)
            self.judge(_METHOD_FUNC(item), f"{path}.__func__", inside)
        elif kind is _ALIAS and not inside:  # its value, cached when first read, is held by neither
            self.reject(path, "a type alias of the pack's (its cached value is held by no guard)")
        elif id(kind) in self.typing:
            self.admit("typing")
            for index, value in enumerate(self.referents(item, kind)):
                if value is kind:
                    continue
                if type(value) is dict:
                    self.items(value, path, True)
                else:
                    self.judge(value, f"{path}<{index}>", True)
        elif kind is MappingProxyType:
            self.proxy(item, path, inside)
        else:
            self.instance(item, kind, path, inside)

    def referents(self, item: object, kind: type) -> list[object]:
        """``item``'s referents, its own ``__dict__`` materialised first (through its class's C
        getset), so that a path does not change once a third party has asked for it."""
        self.kinds.own_dict(item, kind)
        return gc.get_referents(item)

    def owned(self, kind: type) -> bool:
        """A class of the pack's: its module the pack's, or a class its module does not bind
        under its qualified name (one that says another module's name, made where it is not)."""
        return self.kinds.of(kind).own or not self.registry.binds(kind)

    def look_through(self, item: object, path: str) -> None:
        """What a stop-set object holds, through other stop-set objects and plain containers
        (exact tuples, lists, dicts, sets and frozensets): everything else judged by the rules,
        inside a foreign holder (the stop set waives its members' own type alone: a bound
        method's ``__self__``, a partial's arguments, a validator's function are each judged,
        and a pack-owned object in full, whatever path reached it)."""
        for index, value in enumerate(self.referents(item, type(item))):
            kind = type(value)
            at = f"{path}<{index}>"
            if id(kind) in ATOMS:
                continue
            if id(kind) in self.stop or any(kind is plain for plain in _PLAIN):
                if id(value) not in self.through:
                    self.through[id(value)] = value
                    if id(kind) in self.stop:
                        self.stopped_by(kind)
                    self.look_through(value, at)
            else:
                self.judge(value, at, True)

    def proxy(self, item: object, path: str, inside: bool) -> None:
        [inner] = gc.get_referents(item)
        if type(inner) is not dict:
            self.reject(path, "a mappingproxy over a mapping that is not a dict")
            return
        self.admit("mappingproxy")
        exempt = id(item) in self.exempt
        for index, (key, value) in enumerate(_DICT_ITEMS(inner)):
            self.judge(key, f"{path}[key {index}]", inside)
            at = f"{path}[{self.key(key, index)}]"
            if exempt and (type(value) is dict or type(value) is list):
                self.json(value, at, inside)
            else:
                self.judge(value, at, inside)

    def instance(self, item: object, kind: type, path: str, inside: bool) -> None:
        """An instance: admitted by identity (``EXEMPT``) or by its class's rule, then looked
        into; a pack-owned one in full (the context of a foreign holder ends at it)."""
        facts = self.kinds.of(kind)
        if id(item) in self.exempt:
            rule = "exempt"
        elif facts.own and facts.namedtuple:
            rule = "pack NamedTuple"
        elif facts.own and facts.frozen_dataclass:
            rule = "pack frozen dataclass"
        elif facts.frozen_model:
            rule = "frozen pydantic model"
        else:
            self.refuse(path, item)
            return
        self.admit(rule)
        model = rule in ("exempt", "frozen pydantic model")
        inside = False if facts.own else inside or model
        if rule == "pack NamedTuple":
            for index, value in enumerate(_TUPLE_COPY(item, _ALL)):
                self.judge(value, f"{path}[{index}]", inside)
        own = self.kinds.own_dict(item, kind)
        slots: dict[int, str] = {}
        for name, descriptor in facts.model_slots:
            try:
                held = _MEMBER_GET(descriptor, item, kind)
            except AttributeError:  # a slot never set
                continue
            except TypeError:  # a member descriptor of another class, planted under the name
                self.reject(f"{path}.{name}", "a model slot that is not its class's")
                continue
            if id(type(held)) not in ATOMS:
                slots[id(held)] = name
        for index, value in enumerate(gc.get_referents(item)):
            if value is kind:
                continue
            if own is not None and value is own:  # the owner's slot: its own __dict__
                self.items(own, path, inside, json=model)
            elif id(value) in slots:  # a pydantic model's own slots, each by its name
                self.model_slot(slots[id(value)], value, path, inside)
            else:
                self.judge(value, f"{path}<{index}>", inside)

    def model_slot(self, name: str, value: object, path: str, inside: bool) -> None:
        """A pydantic model's fields set (exact ``str`` alone), extra (JSON) or private dict."""
        at = f"{path}.{name}"
        if name == "__pydantic_fields_set__":
            if type(value) is set and all(type(n) is str for n in gc.get_referents(value)):
                self.admit("fields set")
            else:
                self.judge(value, at, inside)
        elif type(value) is dict:
            self.items(value, at, inside, json=name == "__pydantic_extra__")
        else:
            self.judge(value, at, inside)

    def bound(self, bound: object, path: str, inside: bool) -> None:
        """A callable's ``__self__``: admitted only where the object itself would be (a
        registered module, a class, an atom, an exact tuple or frozenset, an exempt object, a
        frozen model or a frozen instance of the pack's), and judged as it would be; inside a
        foreign holder, one of the stop set is looked through."""
        kind = type(bound)
        facts = None if id(kind) in ATOMS or _is_class(kind) else self.kinds.of(kind)
        if (
            facts is None
            or kind is tuple
            or kind is frozenset
            or (
                kind is ModuleType and self.registry.module_kind(bound, self.roots) is not None  # pyright: ignore[reportArgumentType]
            )
            or id(bound) in self.exempt
            or facts.frozen_model
            or (facts.own and (facts.frozen_dataclass or facts.namedtuple))
        ):
            self.admit("bound callable")
            self.judge(bound, path, inside)
        elif inside and id(kind) in self.stop:
            self.stopped_by(kind)
            self.look_through(bound, path)
        else:
            self.reject(path, f"a callable bound to {self.name(kind)}")

    def stopped_by(self, kind: type) -> None:
        self.admit("stop set")
        name = self.name(kind)
        self.stopped[name] = self.stopped.get(name, 0) + 1

    def json(self, item: dict[Any, Any] | list[Any], path: str, inside: bool) -> None:
        if (id(item), inside) in self.seen:
            return
        self.seen[(id(item), inside)] = item
        self.admit("JSON container")
        if type(item) is dict:
            self.items(item, path, inside, json=True)
            return
        for index, value in enumerate(_LIST_COPY(item, _ALL)):
            at = f"{path}[{index}]"
            if type(value) is dict or type(value) is list:
                self.json(value, at, inside)  # pyright: ignore[reportArgumentType]
            else:
                self.judge(value, at, inside)

    def exact(self, value: object, base: type, path: str, what: str) -> bool:
        """Whether ``value`` is ``None`` or of ``base`` exactly; else it is refused (a subclass of
        a container where the base type is read is never iterated: its code would run, or it
        would hide what it holds)."""
        if value is None or type(value) is base:
            return value is not None
        self.reject(path, f"a function's {what} that is not an exact {base.__name__}")
        return False

    def function(self, function: FunctionType, path: str) -> None:
        """A function of the pack: its defaults, keyword defaults, closure cells and ``__dict__``,
        each judged; each read through the base type's own getter, and each container refused
        where it is not of its base type exactly."""
        if id(function) in self.rooted:
            return
        self.rooted.add(id(function))
        defaults = _FUNC_DEFAULTS(function)
        if self.exact(defaults, tuple, f"{path}.__defaults__", "__defaults__"):
            for index, value in enumerate(_TUPLE_COPY(defaults, _ALL)):
                self.judge(value, f"{path}.__defaults__[{index}]")
        keywords = _FUNC_KWDEFAULTS(function)
        if self.exact(keywords, dict, f"{path}.__kwdefaults__", "__kwdefaults__"):
            self.items(keywords, f"{path}.__kwdefaults__", False)
        filename = _CODE_FILENAME(_FUNC_CODE(function))
        guard = type(filename) is str and str.__eq__(filename, dataclasses.__file__)
        closure = _FUNC_CLOSURE(function)
        if self.exact(closure, tuple, f"{path}.__closure__", "__closure__"):
            for index, cell in enumerate(_TUPLE_COPY(closure, _ALL)):
                if type(cell) is not CellType:
                    self.reject(f"{path}.cell[{index}]", "a closure item that is not a cell")
                    continue
                try:
                    value = _CELL(cell)
                except ValueError:  # a cell not yet filled
                    continue
                if guard and type(value) is set and not gc.get_referents(value):
                    self.admit("repr_running")
                    continue
                self.judge(value, f"{path}.cell[{index}]")
        own = _FUNC_DICT(function)
        if self.exact(own, dict, f"{path}.__dict__", "__dict__"):
            self.items(own, f"{path}.__dict__", False)
        annotations = _FUNC_ANNOTATIONS(function)  # materialised, as any reader does
        read = (_FUNC_CODE(function), _FUNC_GLOBALS(function), _FUNC_BUILTINS(function))
        read += (defaults, keywords)
        read += (closure, own)
        for index, value in enumerate(gc.get_referents(function)):
            if any(value is known for known in read):
                continue
            if value is annotations and type(value) is dict:
                self.items(value, f"{path}.__annotations__", True)
            else:  # its names, module, doc and type parameters
                self.judge(value, f"{path}<{index}>")

    def klass(self, kind: type, path: str) -> None:
        """A class of the pack: its own names and keys, its machinery by its shape."""
        if id(kind) in self.rooted:
            return
        self.rooted.add(id(kind))
        self.judge(_TYPE_NAME(kind), f"{path}.__name__")
        self.judge(_TYPE_QUALNAME(kind), f"{path}.__qualname__")
        descriptors = _ids((_TUPLEGETTER, GetSetDescriptorType, MemberDescriptorType))
        descriptors |= _ids((WrapperDescriptorType,))
        for index, (key, value) in enumerate(_DICT_ITEMS(class_dict(kind))):
            at = f"{path}.{self.key(key, index)}"
            if id(type(key)) not in ATOMS:
                self.judge(key, f"{path}[key {index}]")
            if type(key) is str and key in CLASS_MACHINERY:
                self.class_machinery(kind, key, value, at)
            elif type(key) is str and key == "__slotnames__":
                if _slotnames_kept(kind, value):
                    self.admit("__slotnames__")
                else:
                    self.reject(at, "a __slotnames__ that is not copyreg's")
            elif id(type(value)) in descriptors:
                self.admit("slot descriptor")
            else:
                self.judge(value, at)

    def class_machinery(self, kind: type, key: str, value: object, path: str) -> None:
        """A machinery name of a class, admitted only in its shape, what it holds judged."""
        if key == "_fields":
            ok = _sub(kind, tuple) and type(value) is tuple
            ok = ok and all(type(i) is str for i in _TUPLE_COPY(value, _ALL))
        elif key == "_abc_impl":
            ok = any(type(value) is known for known in self.machinery[key])
        elif key == "__dataclass_params__":
            ok = type(value) is _PARAMS
            if ok:
                for index, held in enumerate(gc.get_referents(value)):
                    self.judge(held, f"{path}<{index}>")
        elif key == "__dataclass_fields__":
            ok = type(value) is dict
            if ok:
                for index, (name, field) in enumerate(_DICT_ITEMS(value)):
                    at = f"{path}[{self.key(name, index)}]"
                    if not any(type(field) is known for known in self.machinery["fields"]):
                        self.reject(at, "a dataclass field that is not a Field")
                        continue
                    for place, held in enumerate(gc.get_referents(field)):
                        if any(held is known for known in self.machinery["sentinels"]):
                            continue
                        self.judge(held, f"{at}<{place}>")
        else:  # __annotations__, _field_defaults: dicts whose values are judged
            ok = type(value) is dict
            if ok:
                self.items(value, path, key == "__annotations__")
        if ok:
            self.admit("class machinery")
        else:
            self.reject(path, f"a class's {key} not of its shape")


_PLAIN = (tuple, list, dict, set, frozenset)
"""The plain containers the allow-list looks through, inside a stop-set object."""


def pack_modules() -> list[ModuleType]:
    """The pack's modules, each imported, the package first, then by name."""
    import importlib
    import pkgutil

    package = importlib.import_module(PREFIX)
    found = [package]
    for info in sorted(pkgutil.walk_packages(package.__path__, PREFIX + "."), key=lambda i: i.name):
        found.append(importlib.import_module(info.name))
    return found


def allowed(
    modules: Sequence[ModuleType], exempt: Mapping[int, object], names: frozenset[str]
) -> Judge:
    """The allow-list over ``modules`` (the pack's, or modules named under ``PREFIX``): what it
    refused (``rejects``) and how many objects each rule admitted (``admitted``)."""
    judge = Judge(exempt, names, _ids(modules))  # pyright: ignore[reportArgumentType]
    for module in modules:
        name = _lookup(_MODULE_DICT(module), "__name__")
        judge.module(module, name if type(name) is str else "?")
    return judge


# --- The snapshot --------------------------------------------------------------------------------


def _atom(item: object, kind: type) -> str:
    """The content of an object of an exact atom type, as text."""
    if kind is str:
        return "str:" + item  # pyright: ignore[reportOperatorIssue]
    if kind is bytes:
        return "bytes:" + bytes.hex(item)  # pyright: ignore[reportArgumentType]
    if kind is int or kind is bool:
        return f"{_TYPE_QUALNAME(kind)}:{hex(_INT_INDEX(item))}"
    if kind is float:
        return "float:" + _FLOAT_HEX(item)
    if kind is complex:
        return "complex:" + ",".join(_FLOAT_HEX(part(item)) for part in _COMPLEX_PARTS)
    return str(_TYPE_QUALNAME(kind))


def _content(item: object, kind: type) -> str | None:
    """The content of a text, number or buffer of a subclass or of a type the snapshot reads
    whole, through its base type's own method; ``None`` for any other object."""
    if _sub(kind, str):
        return "str:" + _STR_COPY(item, _ALL)
    if _sub(kind, bytes):
        return "bytes:" + bytes.hex(_BYTES_COPY(item, _ALL))
    if _sub(kind, int):
        return "int:" + hex(_INT_INDEX(item))
    if _sub(kind, float):
        return "float:" + _FLOAT_HEX(item)
    if _sub(kind, complex):
        return "complex:" + ",".join(_FLOAT_HEX(part(item)) for part in _COMPLEX_PARTS)
    if _sub(kind, bytearray):
        return "bytearray:" + _BYTEARRAY_HEX(item)
    if _sub(kind, array):
        return f"array {_ARRAY_TYPECODE(item)}:" + _ARRAY_TOBYTES(item).hex()
    if kind is memoryview:
        try:
            return "memoryview:" + _MEMORYVIEW_TOBYTES(item).hex()
        except ValueError:  # released
            return "memoryview: released"
    if _sub(kind, io.StringIO):
        try:
            return "StringIO:" + _STRINGIO_GETVALUE(item)
        except ValueError:  # closed, or never initialised
            return "StringIO: closed"
    if _sub(kind, io.BytesIO):
        try:
            return "BytesIO:" + _BYTESIO_GETVALUE(item).hex()
        except ValueError:
            return "BytesIO: closed"
    return None


def _module_label(module: object) -> str:
    name = _lookup(_MODULE_DICT(module), "__name__")  # pyright: ignore[reportArgumentType]
    return name if type(name) is str else "?"


class _Pass:
    """One snapshot: ``found``, path to digest; ``seen``, every object entered, by id, with the
    first path that reached it, the object kept alive for the pass (an id is unique only among
    the living: the pass makes no temporary it enters today, a class's dict being read behind
    its mappingproxy, and an object it entered and let go would give its id to another)."""

    def __init__(self, roots: Sequence[object], names: frozenset[str]) -> None:
        self.names = names
        self.kinds = _Kinds()
        self.found: dict[str, int] = {}
        self.seen: dict[int, tuple[str, object]] = {}
        self.modules = _ids(r for r in roots if type(r) is ModuleType)  # pyright: ignore[reportArgumentType]
        self.registry = Registry()
        self.abc = _ids(_machinery_types()["_abc_impl"])  # pyright: ignore[reportArgumentType]
        self.stack: list[tuple[str, object, type | None]] = []
        for index, root in reversed(list(enumerate(roots))):
            path = _module_label(root) if type(root) is ModuleType else f"root {index}"
            self.stack.append((path, root, None))

    def key(self, key: object, index: int) -> str:
        return label(key, self.names) if type(key) is str else f"#{index}"

    def put(self, path: str, text: str) -> None:
        self.found[path] = hash(text)  # the process's keyed hash of an exact str: never the text

    def run(self) -> dict[str, int]:
        while self.stack:
            path, item, owner = self.stack.pop()
            self.enter(path, item, owner)
        return self.found

    def push(self, path: str, item: object, owner: type | None = None) -> None:
        self.stack.append((path, item, owner))

    def enter(self, path: str, item: object, owner: type | None) -> None:
        kind = type(item)
        if id(kind) in ATOMS:
            self.put(path, _atom(item, kind))
            return
        first = self.seen.get(id(item))
        if first is not None:
            self.put(path, "ref:" + first[0])
            return
        self.seen[id(item)] = (path, item)
        if kind is ModuleType:
            found = self.registry.module_kind(item, self.modules)  # pyright: ignore[reportArgumentType]
            if found == "library module":  # another library's: by its name
                self.put(path, "module:" + _module_label(item))
            else:  # a root, the core's, or one the pack made (registered or not): entered
                self.put(path, "module")
                self.push(f"{path}.D", _MODULE_DICT(item))  # pyright: ignore[reportArgumentType]
        elif id(kind) in self.abc:  # abc's cache, which isinstance and issubclass write
            self.put(path, "abc data")
        elif kind is _ALIAS:  # a type alias: its function, never the value it caches when read
            self.put(path, "type alias")
            held = [r for r in gc.get_referents(item) if type(r) is FunctionType]
            for index, value in reversed(list(enumerate(held))):
                self.push(f"{path}<{index}>", value)
        elif _is_class(kind):
            facts = self.kinds.of(item)  # pyright: ignore[reportArgumentType]
            if facts.core or not self.registry.binds(item):  # pyright: ignore[reportArgumentType]
                self.put(path, "class")
                self.push(f"{path}.D", class_dict(item), item)  # pyright: ignore[reportArgumentType]
            else:
                self.put(path, "class:" + facts.name)
        elif kind is FunctionType:
            self.function(path, item)  # pyright: ignore[reportArgumentType]
        elif kind is CodeType:
            self.put(path, "code")
        elif kind is MappingProxyType:
            self.proxy(path, item)
        elif owner is not None and kind is dict:
            self.class_items(path, item, owner)
        elif _sub(kind, dict):
            self.mapping(path, item, kind)
        elif _sub(kind, list) or _sub(kind, tuple):
            base = list if _sub(kind, list) else tuple
            items = _LIST_COPY(item, _ALL) if base is list else _TUPLE_COPY(item, _ALL)
            self.put(path, f"{base.__name__}:{self.kinds.of(kind).name}:{len(items)}")
            for index, value in reversed(list(enumerate(items))):
                self.push(f"{path}[{index}]", value)
            self.own(path, item, kind)
        elif _sub(kind, set) or _sub(kind, frozenset):
            members = gc.get_referents(item)
            atoms = sorted(_digest(_atom(m, type(m))) for m in members if id(type(m)) in ATOMS)
            self.put(path, f"set:{self.kinds.of(kind).name}:{len(members)}:{','.join(atoms)}")
            others = [m for m in members if id(type(m)) not in ATOMS]
            for index, value in reversed(list(enumerate(others))):
                self.push(f"{path}{{{index}}}", value)
            self.own(path, item, kind)
        else:
            content = _content(item, kind)
            if content is not None:
                self.put(path, f"{content}:{self.kinds.of(kind).name}")
                self.own(path, item, kind)
                return
            own = self.kinds.own_dict(item, kind)
            held = [r for r in gc.get_referents(item) if r is not kind and r is not own]
            name = self.kinds.of(kind).name
            self.put(path, f"object:{name}:{len(held)}:{own is not None}")
            if own is not None:
                self.push(f"{path}.__dict__", own)
            for index, value in reversed(list(enumerate(held))):
                self.push(f"{path}<{index}>", value)

    def own(self, path: str, item: object, kind: type) -> None:
        """The own ``__dict__`` of an instance of a subclass of a type read whole."""
        if self.kinds.of(kind).module != "builtins":
            own = self.kinds.own_dict(item, kind)
            if own is not None:
                self.push(f"{path}.__dict__", own)

    def function(self, path: str, function: FunctionType) -> None:
        """A function of ``aibi`` entered (its defaults, keyword defaults, closure, annotations,
        ``__dict__`` and names, but its code and globals); another's, by name."""
        if not self.registry.of_core(function):
            qualname = _FUNC_QUALNAME(function)
            self.put(path, "function:" + (qualname if type(qualname) is str else "?"))
            return
        self.put(path, "function")
        _FUNC_ANNOTATIONS(function)  # content, not form: a tuple made a dict, as any read does
        _FUNC_DICT(function)
        code, scope = _FUNC_CODE(function), _FUNC_GLOBALS(function)
        held = [r for r in gc.get_referents(function) if r is not code and r is not scope]
        for index, value in reversed(list(enumerate(held))):
            self.push(f"{path}<{index}>", value)

    def proxy(self, path: str, item: object) -> None:
        """A mappingproxy: the mapping behind it."""
        [inner] = gc.get_referents(item)
        self.put(path, "mappingproxy")
        self.push(f"{path}*", inner)

    def class_items(self, path: str, inner: object, owner: type) -> None:
        """A class's own dict (``owner``'s, the dict behind its mappingproxy, read without one),
        without a ``__slotnames__`` that is ``copyreg``'s (content, not representation)."""
        items = [
            (key, value)
            for key, value in _DICT_ITEMS(inner)
            if not (type(key) is str and key == "__slotnames__" and _slotnames_kept(owner, value))
        ]
        self.items(path, f"class dict:{len(items)}", items)

    def mapping(self, path: str, item: object, kind: type) -> None:
        items = list(_DICT_ITEMS(item))
        self.items(path, f"dict:{self.kinds.of(kind).name}:{_DICT_LEN(item)}", items)
        self.own(path, item, kind)

    def items(self, path: str, text: str, items: list[tuple[object, object]]) -> None:
        self.put(path, text)
        for index, (key, value) in reversed(list(enumerate(items))):
            self.push(f"{path}[{self.key(key, index)}]", value)
            if type(key) is not str:
                self.push(f"{path}[key {index}]", key)


def snapshot(roots: Sequence[object], names: frozenset[str]) -> dict[str, int]:
    """The snapshot of what ``roots`` (the pack's modules, or a test's own roots) hold: a digest
    of each node's own content, by its path (the interpreter's hash of an exact ``str`` of it,
    SipHash under the process's own key: a snapshot is compared within one process alone, and
    never reported; a path's labels, which are, are keyed blake2b digests)."""
    return _Pass(roots, names).run()


def differences(first: Mapping[str, int], now: Mapping[str, int]) -> list[str]:
    """The paths whose digest differs between two snapshots, or that one of them lacks."""
    return sorted(path for path in {*first, *now} if first.get(path) != now.get(path))


# --- The guard the scan's child runs -------------------------------------------------------------


class Guard:
    """The allow-list and the snapshot over the pack's modules, made once the pack is imported
    and before the first call (its rejects ``after`` 0, its first snapshot), then ``after`` each
    call: the allow-list's rejects that were not there before the first call, and the paths
    whose digest differs from the **first** snapshot's."""

    def __init__(self, modules: Sequence[ModuleType], extra: Sequence[object] = ()) -> None:
        self.modules = list(modules)
        self.roots = [*self.modules, *extra]
        self.names = pack_names(self.modules)
        self.exempt = exemptions(pack_modules())
        self.rejects: list[tuple[int, Reject]] = []
        self.changed: list[tuple[int, int, tuple[str, ...]]] = []
        judged = allowed(self.modules, self.exempt, self.names)
        self.known = frozenset(judged.rejects)
        self.rejects += [(0, reject) for reject in judged.rejects]
        self.first = snapshot(self.roots, self.names)

    def after(self, done: int) -> None:
        judged = allowed(self.modules, self.exempt, self.names)
        self.rejects += [(done, r) for r in judged.rejects if r not in self.known]
        now = snapshot(self.roots, self.names)
        found = differences(self.first, now)
        if found:
            self.changed.append((done, len(found), tuple(found[:4])))

    def own(self) -> list[object]:
        """The guard's stores, for the walk to skip (it would read every path and digest)."""
        return [self, vars(self), self.first, self.rejects, self.changed, self.known]

    def report(self) -> dict[str, object]:
        return {
            "rejects": [{"after": a, "path": r.path, "kind": r.kind} for a, r in self.rejects],
            "differences": [
                {"after": a, "count": n, "paths": list(paths)} for a, n, paths in self.changed
            ],
            "nodes": len(self.first),
        }


__all__ = [
    "CLASS_MACHINERY", "EXEMPT", "MODULE_MACHINERY", "RULES", "TYPING", "Guard", "Judge",
    "Reject", "Rule", "allowed", "differences", "exemptions", "label", "names_of",
    "pack_modules", "pack_names", "slotnames", "snapshot", "stop_set",
]  # fmt: skip

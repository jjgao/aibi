"""What a copy of the core's is made of, and what it shares with what a pack built (D388): helpers
for the copiers' and the hooks' tests.

- ``core_made`` walks a value the core keeps: every object is of an exact built-in type, a real
  member of an enum of ``aibi.core.schema`` (by identity), or an instance of a class the core
  defines (found by identity in ``CORE_CLASSES``, never by ``__module__``) whose instance
  dictionary is a plain ``dict`` of exact-``str`` keys and, for a pydantic model, whose
  ``__pydantic_fields_set__`` is an exact ``set`` of exact ``str``, its ``__pydantic_extra__``, if
  any, walked as a ``dict``.
- ``shared`` walks two object graphs by ``gc.get_referents``, which runs no code of the pack's,
  and gives what they share that may not be shared: anything but exact immutable atoms, real core
  enum members, and tuples and frozensets of those. The walk stops at types, modules, functions,
  methods, descriptors and code, which every object reaches through its class.
"""

import dataclasses
import gc
import importlib
import inspect
import pkgutil
import types
from collections.abc import Iterable
from enum import Enum
from typing import Any, cast

from pydantic import BaseModel

import aibi.core

_ATOMS: tuple[type, ...] = (str, int, float, bool, bytes, type(None))


def _core_modules() -> list[types.ModuleType]:
    found: list[types.ModuleType] = []
    for module in pkgutil.walk_packages(aibi.core.__path__, "aibi.core."):
        if module.name.startswith(("aibi.core.engine.duck", "aibi.core.importers.snapshot")):
            continue  # they load DuckDB, which the server's process never does
        found.append(importlib.import_module(module.name))
    return found


def _schema_enums() -> tuple[type[Enum], ...]:
    found: list[type[Enum]] = []
    for module in pkgutil.walk_packages(
        importlib.import_module("aibi.core.schema").__path__, "aibi.core.schema."
    ):
        loaded = importlib.import_module(module.name)
        for _, value in inspect.getmembers(loaded, inspect.isclass):
            if issubclass(value, Enum) and value.__module__ == loaded.__name__:
                found.append(value)
    return tuple(found)


SCHEMA_ENUMS: tuple[type[Enum], ...] = _schema_enums()
"""Every enum class ``aibi.core.schema``'s modules define."""
MEMBERS: dict[int, Enum] = {id(m): m for kind in SCHEMA_ENUMS for m in kind}


def _core_classes() -> frozenset[int]:
    found: set[int] = set()
    for loaded in _core_modules():
        for _, value in inspect.getmembers(loaded, inspect.isclass):
            if value.__module__ == loaded.__name__:
                found.add(id(value))
    return frozenset(found)


CORE_CLASSES: frozenset[int] = _core_classes()
"""The ids of every class the core's modules define, so that a class is the core's by identity,
which a pack cannot forge by setting ``__module__``."""


def real_member(value: object) -> bool:
    return MEMBERS.get(id(value)) is value


def core_made(value: object, seen: set[int] | None = None) -> None:
    """Assert that ``value`` is made of the core's objects alone (module docstring)."""
    seen = set() if seen is None else seen
    kind = type(value)
    if any(kind is atom for atom in _ATOMS):
        return
    if any(kind is enum for enum in SCHEMA_ENUMS):
        assert real_member(value), "a fake enum member"
        return
    if id(value) in seen:
        return
    seen.add(id(value))
    if kind is dict:
        for key, member in cast(dict[object, object], value).items():
            core_made(key, seen)
            core_made(member, seen)
        return
    if kind is list or kind is tuple or kind is frozenset:
        for item in cast(Iterable[object], value):
            core_made(item, seen)
        return
    assert id(kind) in CORE_CLASSES, f"a {kind.__name__} the core did not define"
    held = cast(object, vars(value))
    assert type(held) is dict, "an instance dictionary of another type"
    assert all(type(key) is str for key in cast(dict[object, object], held))
    if isinstance(value, BaseModel):
        given = cast(object, value.__pydantic_fields_set__)
        assert type(given) is set
        assert all(type(name) is str for name in cast(set[object], given))
        extra = cast(object, value.__pydantic_extra__)
        assert extra is None or type(extra) is dict
        if extra is not None:
            core_made(extra, seen)
    for member in cast(dict[str, object], held).values():
        core_made(member, seen)
    if dataclasses.is_dataclass(value):
        for field in dataclasses.fields(value):
            core_made(getattr(value, field.name), seen)


_STOPS: tuple[type, ...] = (
    type,
    types.ModuleType,
    types.FunctionType,
    types.BuiltinFunctionType,
    types.MethodType,
    types.MethodWrapperType,
    types.WrapperDescriptorType,
    types.MethodDescriptorType,
    types.GetSetDescriptorType,
    types.MemberDescriptorType,
    types.ClassMethodDescriptorType,
    types.CodeType,
    property,
    staticmethod,
    classmethod,
)


def _stop(value: object) -> bool:
    return isinstance(value, _STOPS) or real_member(value)


def _allowed(value: object) -> bool:
    kind = type(value)
    if any(kind is atom for atom in _ATOMS) or real_member(value):
        return True
    if kind is tuple or kind is frozenset:
        return all(_allowed(item) for item in cast(Iterable[object], value))
    return False


def reachable(roots: Iterable[object]) -> dict[int, object]:
    """Every object reachable from ``roots`` by ``gc.get_referents``, not past the stop set."""
    found: dict[int, object] = {}
    pending = list(roots)
    while pending:
        current = pending.pop()
        if id(current) in found:
            continue
        found[id(current)] = current
        if _stop(current):
            continue
        pending.extend(gc.get_referents(current))
    return found


def shared(given: Iterable[object], copy: object) -> list[object]:
    """What the copy shares with ``given``'s graph but may not (module docstring)."""
    theirs = reachable(given)
    ours = reachable([copy])
    return [
        value
        for key, value in ours.items()
        if key in theirs and not _allowed(value) and not _stop(value)
    ]


def holds_no_handle(values: Iterable[object]) -> bool:
    """Whether no object reachable from ``values`` is a hook's handle (D388)."""
    from aibi.core.schema.guards import Hook

    return not any(type(value) is Hook for value in reachable(values).values())


Kept = list[Any]

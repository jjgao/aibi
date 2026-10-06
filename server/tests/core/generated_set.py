"""The generated set of D399: every type that holds server text, found by reflection.

A type is in the set when it is a Pydantic model or a dataclass (or a ``TypedDict`` or
``NamedTuple``) defined in the package walked, and one of its members' annotations names
``TextSegment`` or a type already in the set: the transitive closure, with the subclasses of a
model in it. The tests of the guard run over every Pydantic model of the set, and the writers of
its dataclasses are tested at each consumer. A plain class that carries a set member (an
exception holding refusals) or a ``Protocol`` returning one is not walked: each is named, with
why, by the test that computes the set, and a new one fails it.
"""

import dataclasses
import importlib
import inspect
import pkgutil
import typing
from collections.abc import Callable, Iterable
from types import ModuleType
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from aibi.core.schema.output import TextSegment


def modules(package: ModuleType) -> list[ModuleType]:
    """Every module of ``package``."""
    found = [package]
    for info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        found.append(importlib.import_module(info.name))
    return found


def _defined(module: ModuleType) -> Iterable[type]:
    for value in vars(module).values():
        if isinstance(value, type) and value.__module__ == module.__name__:
            yield value


def _structured(kind: type) -> bool:
    return (
        issubclass(kind, BaseModel)
        or dataclasses.is_dataclass(kind)
        or typing.is_typeddict(kind)
        or (issubclass(kind, tuple) and hasattr(kind, "_fields"))
    )


def _hints(owner: object) -> list[object]:
    try:
        return list(typing.get_type_hints(owner, include_extras=True).values())
    except Exception:  # an unresolvable forward reference: the raw annotations
        return list(getattr(owner, "__annotations__", {}).values())


def annotations(kind: type) -> list[object]:
    """The annotations of a structured type's members."""
    if issubclass(kind, BaseModel):
        return [info.annotation for info in kind.model_fields.values()]
    return _hints(kind)


def mentions(annotation: object, targets: set[type], seen: set[int] | None = None) -> bool:
    """Whether ``annotation`` names one of ``targets`` anywhere inside."""
    seen = set() if seen is None else seen
    if id(annotation) in seen:
        return False
    seen.add(id(annotation))
    if isinstance(annotation, type) and annotation in targets:
        return True
    if isinstance(annotation, typing.TypeAliasType):
        return mentions(annotation.__value__, targets, seen)
    origin = get_origin(annotation)
    if isinstance(origin, type) and origin in targets:
        return True
    return any(mentions(argument, targets, seen) for argument in get_args(annotation))


def structured(package: ModuleType) -> dict[str, type]:
    """The structured types ``package`` defines, by qualified name."""
    return {
        f"{kind.__module__}.{kind.__qualname__}": kind
        for module in modules(package)
        for kind in _defined(module)
        if _structured(kind)
    }


def generated_set(package: ModuleType, seeds: Iterable[type] = ()) -> dict[str, type]:
    """The types of ``package`` that hold server text, by qualified name, ``TextSegment`` left
    out; ``seeds`` are such types of another package."""
    types = structured(package)
    bearing: set[type] = {TextSegment, *seeds}
    changed = True
    while changed:
        changed = False
        for kind in types.values():
            if kind in bearing:
                continue
            if any(mentions(annotation, bearing) for annotation in annotations(kind)) or (
                issubclass(kind, BaseModel)
                and any(isinstance(b, type) and issubclass(kind, b) for b in bearing)
            ):
                bearing.add(kind)
                changed = True
    return {
        name: kind for name, kind in types.items() if kind in bearing and kind is not TextSegment
    }


def carriers(package: ModuleType, bearing: set[type]) -> dict[str, str]:
    """The plain classes of ``package`` whose constructor takes a type of ``bearing``, and the
    ``Protocol`` classes one of whose methods returns one: by qualified name, what each is."""
    found: dict[str, str] = {}
    for module in modules(package):
        for kind in _defined(module):
            name = f"{kind.__module__}.{kind.__qualname__}"
            if _structured(kind):
                continue
            if getattr(kind, "_is_protocol", False):
                for member in vars(kind).values():
                    if callable(member) and _returns(member, bearing):
                        found[name] = "protocol"
                continue
            init = kind.__dict__.get("__init__")
            if init is not None and any(mentions(hint, bearing) for hint in _hints(init)):
                found[name] = "carrier"
    return found


def _returns(member: Callable[..., Any], bearing: set[type]) -> bool:
    try:
        hints = typing.get_type_hints(member, include_extras=True)
    except Exception:
        hints = getattr(inspect.unwrap(member), "__annotations__", {})
    return "return" in hints and mentions(hints["return"], bearing)

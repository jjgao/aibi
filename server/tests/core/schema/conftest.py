"""Helpers shared by the schema tests."""

from collections.abc import Callable, Iterator
from typing import Annotated, get_args, get_origin

import pytest
from annotated_types import MaxLen
from pydantic import BaseModel, StringConstraints
from pydantic.fields import FieldInfo

from aibi.core.schema.limits import LimitName


def _annotations(root: object) -> Iterator[tuple[str, list[object]]]:
    """Every annotation reachable from a model or annotation, with its Annotated metadata."""
    seen: set[int] = set()
    pending: list[tuple[str, object, list[object]]] = [
        (getattr(root, "__name__", "root"), root, [])
    ]
    while pending:
        where, annotation, metadata = pending.pop()
        if id(annotation) in seen and not metadata:
            continue
        seen.add(id(annotation))
        if get_origin(annotation) is Annotated:
            inner, *extra = get_args(annotation)
            pending.append((where, inner, [*metadata, *extra]))
            continue
        yield where, metadata
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            for name, info in annotation.model_fields.items():
                pending.append((f"{annotation.__name__}.{name}", info.annotation, info.metadata))
        else:
            pending.extend((where, arg, []) for arg in get_args(annotation))


def _caps_length(extra: object) -> bool:
    if isinstance(extra, MaxLen):
        return True
    if isinstance(extra, StringConstraints):
        return extra.max_length is not None
    if isinstance(extra, FieldInfo):
        return any(_caps_length(inner) for inner in extra.metadata)
    return False


def _length_caps(root: object) -> list[tuple[str, bool]]:
    """Each length cap reachable from ``root``, and whether it names its limit."""
    return [
        (where, any(isinstance(extra, LimitName) for extra in metadata))
        for where, metadata in _annotations(root)
        if any(_caps_length(extra) for extra in metadata)
    ]


@pytest.fixture
def length_caps() -> Callable[[object], list[tuple[str, bool]]]:
    return _length_caps

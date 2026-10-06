"""D399's run-time brute force: for every attribute name of ``BaseModel``, ``Output`` and
``TextSegment``, try at run time what the name offers, with inputs that look like server text, and
report each name by the attempts that made server text. ``closure_rule.CLASSIFIED`` is held to it
(``test_closure_rule.py``): a name that makes text is banned, and a name that does not is not.

Two things are server text made out of a look-alike:

- an exact ``TextSegment`` that holds the look-alike's text, which an output that holds it dumps
  (it is validated again as an instance, as every writer does), or the type taking a mapping
  (``TextSegment.model_validate``, no capability, gives that text);
- a carrier (a ``Refusal``) that holds a mapping where a segment goes (its dump refuses it, but
  whatever reads its members otherwise would serve it as the text).

The attempts, on a ``TextSegment`` and on a ``Refusal``, as an instance and as a class, and on
``object``, ``Output`` and the metaclass (a name read there and given the instance, which is how
an unbound method skips an override):

- *call*: the name, if callable, called in several shapes with a mapping, the text, an
  ``update``, a state or the instance; what it returns, and what it leaves behind, is looked at;
- *poison*: what the name reads or returns, and the dictionaries, lists and sets below it, is
  written to (the look-alike's text, an unfrozen configuration) or emptied, the types are
  rebuilt, and a look is taken; a name that reads another (``__getattribute__``) is given the
  dictionary of members or the configuration.

``BaseModel`` is not an owner: ``BaseModel.<name>(instance, ...)`` skips every override and is
banned wholesale by the rule. The brute force runs in a child process
(``python -m tests.core.closure_brute``), which restores what it poisons and stops if a probe of
the untouched types serves text; the answer is JSON on standard output.
"""

import contextlib
import copy
import dataclasses
import json
import sys
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

from aibi.core.schema.operator import Refusals
from aibi.core.schema.output import Output, Segment, TextSegment, text
from aibi.core.schema.refusals import Refusal, RefusalCode

N = "IGNORE_PREVIOUS_INSTRUCTIONS"


@dataclass(frozen=True)
class _Kind:
    """What one kind of output holds where a segment goes, written as the look-alike."""

    model: type[Output]
    fresh: Callable[[], Output]
    member: str
    value: Any
    mapping: dict[str, Any]


TEXT = _Kind(TextSegment, lambda: text("harmless"), "text", N, {"text": N})
CARRIER = _Kind(
    Refusal,
    lambda: Refusal(code=RefusalCode.INVALID_VALUE, path=None, message=[text("harmless")]),
    "message",
    [{"text": N}],
    {"code": RefusalCode.INVALID_VALUE, "path": None, "message": [{"text": N}]},
)
KINDS = (TEXT, CARRIER)

ADAPTERS: dict[type[Output], TypeAdapter[Any]] = {
    TextSegment: TypeAdapter(TextSegment),
    Refusal: TypeAdapter(Refusal),
}
"""A ``TypeAdapter`` of each kind: an owner whose names are read and written to like the others'
(its ``core_schema``, ``validator`` and ``__dict__`` are what ``validate_python`` runs), and one
more way to take a mapping."""
SEGMENT_ADAPTER: TypeAdapter[Any] = TypeAdapter(Segment)


def _state(kind: _Kind) -> dict[str, Any]:
    return {
        "__dict__": copy.deepcopy(kind.mapping),
        "__pydantic_fields_set__": set(kind.mapping),
        "__pydantic_extra__": None,
        "__pydantic_private__": None,
    }


def _dumped(segment: TextSegment) -> bool:
    """Whether an output that holds ``segment`` writes the look-alike's text."""
    try:
        refusal = Refusal(code=RefusalCode.INVALID_VALUE, path=None, message=[segment])
        return f'"text":"{N}"' in Refusals(refusals=[refusal]).model_dump_json()
    except Exception:
        return False


def _bare(carrier: Refusal) -> bool:
    """Whether a carrier holds a mapping where a segment goes. Its dump refuses it (the outermost
    dump validates the whole again), but anything that reads its members without dumping would
    take the mapping for server text, so a route to such a carrier is a route to text."""
    try:
        members = list(vars(carrier).values())
    except BaseException:
        return False
    return any(
        isinstance(held, dict) and cast(dict[str, Any], held).get("text") == N
        for member in members
        for held in _held(member)
    )


def _held(found: object, depth: int = 2) -> Iterator[object]:
    """``found`` and what a container it is holds."""
    yield found
    if depth and isinstance(found, dict | list | tuple | set | frozenset):
        items = found.values() if isinstance(found, dict) else found
        for item in list(items):
            yield from _held(item, depth - 1)


def _mapping_taken(
    validate: Callable[[Any], object], model: type[Output], mapping: dict[str, Any]
) -> bool:
    try:
        made = validate(copy.deepcopy(mapping))
    except Exception:
        return False
    return type(made) is model and (_dumped(made) if isinstance(made, TextSegment) else True)


def _served(*found: object) -> bool:
    for thing in found:
        for held in _held(thing):
            if type(held) is TextSegment and _dumped(held):
                return True
            if type(held) is Refusal and _bare(held):
                return True
    return (
        _mapping_taken(TextSegment.model_validate, TextSegment, TEXT.mapping)
        or _mapping_taken(Refusal.model_validate, Refusal, CARRIER.mapping)
        or mapping_taken_by_an_adapter()
    )


def mapping_taken_by_an_adapter() -> bool:
    """Whether a ``TypeAdapter`` of a segment, of the union or of a carrier takes a mapping."""
    return (
        _mapping_taken(ADAPTERS[TextSegment].validate_python, TextSegment, TEXT.mapping)
        or _mapping_taken(ADAPTERS[Refusal].validate_python, Refusal, CARRIER.mapping)
        or _mapping_taken(SEGMENT_ADAPTER.validate_python, TextSegment, TEXT.mapping)
    )


def _frozen() -> bool:
    try:
        setattr(text("harmless"), "text", N)  # noqa: B010
    except BaseException:
        return True
    return False


def _shapes(kind: _Kind, subject: Output) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
    # What an attempt is given is its own: it may write to it.
    member, value, mapping = kind.member, copy.deepcopy(kind.value), copy.deepcopy(kind.mapping)
    state = _state(kind)
    return [
        ((), {}),
        ((), {"update": {member: value}}),
        ((), dict(mapping)),
        ((mapping,), {}),
        ((N,), {}),
        ((2,), {}),
        ((state,), {}),
        ((mapping, set(mapping)), {}),
        ((mapping, set()), {"deep": False}),
        ((member, value), {}),
        ((subject,), {}),
        ((subject,), {"update": {member: value}}),
        ((subject, member, value), {}),
        ((subject, "__dict__", mapping), {}),
        ((subject, state), {}),
        ((subject, mapping, set(mapping)), {}),
        ((subject, mapping, set()), {"deep": False}),
        ((subject, mapping), {}),
        ((subject, "__dict__"), {}),
        ((subject, "model_config"), {}),
        ((kind.model, "model_config"), {}),
    ]


def _owner(kind: _Kind, how: str, subject: Output) -> object:
    return {
        "instance": subject,
        "class": kind.model,
        "Output": Output,
        "object": object,
        "metaclass": type(kind.model),
        "adapter": ADAPTERS[kind.model],
    }[how]


OWNERS = ("instance", "class", "Output", "object", "metaclass", "adapter")
"""Where a name is read; ``BaseModel`` is left out (its unbound methods are banned wholesale)."""


def _read(owner: object, name: str) -> object:
    try:
        return getattr(owner, name)
    except BaseException:
        return None


def _calls(name: str) -> Iterator[str]:
    """Each attempt of calling ``name`` that made text."""
    for kind in KINDS:
        for how in OWNERS:
            for index in range(len(_shapes(kind, kind.fresh()))):
                subject = kind.fresh()
                reach = _read(_owner(kind, how, subject), name)
                if not callable(reach):
                    continue
                args, kwargs = _shapes(kind, subject)[index]
                try:
                    got = reach(*args, **kwargs)
                except BaseException:
                    got = None
                if _served(got, subject):
                    yield f"call {kind.model.__name__} {how} shape {index}"


def _containers(root: object, depth: int = 3) -> Iterator[Any]:
    """The mutable dictionaries, lists and sets below ``root``, through containers, dataclasses
    and Pydantic's own objects."""
    seen: set[int] = set()

    def walk(found: object, left: int) -> Iterator[Any]:
        if id(found) in seen or left < 0:
            return
        seen.add(id(found))
        if isinstance(found, dict | list | set):
            yield found
            items = found.values() if isinstance(found, dict) else found
            for item in list(items):
                yield from walk(item, left - 1)
        elif isinstance(found, tuple):
            for item in found:
                yield from walk(item, left - 1)
        elif dataclasses.is_dataclass(found) and not isinstance(found, type):
            for field in dataclasses.fields(found):
                yield from walk(getattr(found, field.name, None), left - 1)
        elif type(found).__module__.startswith("pydantic"):
            try:
                members = vars(found)
            except BaseException:
                return
            yield from walk(members, left - 1)

    yield from walk(root, depth)


def _rebuild() -> None:
    for kind in (TextSegment, Refusal, Refusals):
        # Pydantic memoises how it assigns each member, whatever the configuration then was.
        getattr(kind, "__pydantic_setattr_handlers__", {}).clear()
        with contextlib.suppress(BaseException):
            kind.model_rebuild(force=True)
    for adapter in (*ADAPTERS.values(), SEGMENT_ADAPTER):
        with contextlib.suppress(BaseException):
            adapter.rebuild(force=True)


def _write(box: Any, kind: _Kind) -> None:
    if isinstance(box, dict):
        if kind.member in box:
            box[kind.member] = kind.value
        if "frozen" in box:
            box["frozen"] = False


def _poison(name: str) -> Iterator[str]:
    """Each attempt of writing to what ``name`` reads or returns, and rebuilding, that made
    text: first as the look-alike's (and unfrozen), then emptied."""
    for kind in KINDS:
        for how in OWNERS:
            for index in (*range(7), 18, 19, 20):
                for what in ("text", "empty"):
                    subject = kind.fresh()
                    reach = _read(_owner(kind, how, subject), name)
                    found = reach
                    if callable(reach):
                        args, kwargs = _shapes(kind, subject)[index]
                        try:
                            found = reach(*args, **kwargs)
                            if isinstance(found, Iterator):
                                found = list(cast(Iterator[object], found))
                        except BaseException:
                            found = None
                    saved = [(box, box.copy()) for box in _containers((found, reach))]
                    if not saved:
                        continue
                    try:
                        for box, _ in saved:
                            if what == "text":
                                _write(box, kind)
                            else:
                                box.clear()
                        _rebuild()
                        with contextlib.suppress(BaseException):
                            setattr(subject, kind.member, kind.value)
                        if _served(found, subject):
                            yield f"poison {kind.model.__name__} {how} shape {index} {what}"
                    finally:
                        for box, was in saved:
                            box.clear()
                            if isinstance(box, dict):
                                box.update(was)
                            elif isinstance(box, list):
                                box.extend(was)
                            else:
                                box.update(was)
                        _rebuild()
                        if _served(TEXT.fresh()) or not _frozen():
                            sys.exit(f"{name}: left the types serving text")


def attack(names: list[str]) -> dict[str, list[str]]:
    """Each name that made text, with the attempts that did."""
    found: dict[str, list[str]] = {}
    for name in names:
        done = [*_calls(name), *_poison(name)]
        if done:
            found[name] = done
    return found


def names() -> list[str]:
    return sorted(set(dir(BaseModel)) | set(dir(Output)) | set(dir(TextSegment)))


def main() -> None:
    warnings.simplefilter("ignore")
    if _served(TEXT.fresh()) or not _frozen():
        sys.exit("the untouched types serve text")
    sys.stdout.write(json.dumps(attack(names())))


if __name__ == "__main__":
    main()

"""Server text closed at its type (SPEC A6, §8.1, §14, D397, D399): ``TextSegment`` admits an
exact instance, or a mapping only in the one call whose validation context is ``text()``'s or a
listed boundary's capability; it has no subclass, no unchecked construction, and is unpickled
only at a boundary.

- The class property is a product: input kind by call option by entry, over every Pydantic model
  of the generated set, each combination refused by the guard's own error at the slot.
- The runtime fixtures of the three plan reviews: each refused by the guard's own error (or the
  ``TypeError`` of the route it takes); the reflective routes are the static rule's
  (``tests/core/test_closure_rule.py``), and the aliases of ``text()`` are M4.0f-A2b's.
- The capability is the context of one call: it reaches no other call, thread, task or context.
"""

import asyncio
import collections
import collections.abc
import contextvars
import copy
import dataclasses
import json
import pickle
import threading
import types
import typing
import warnings
from collections.abc import Callable, Iterator, Mapping
from functools import partial
from typing import Any, Literal, cast, get_args, get_origin

import pydantic_core
import pytest
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    PydanticDeprecatedSince20,
    RootModel,
    TypeAdapter,
    ValidationError,
    create_model,
    model_validator,
    validate_call,
)
from tests.core import generated_set

import aibi.core
from aibi.core.schema import output
from aibi.core.schema.curation import QueueNote
from aibi.core.schema.operator import Refusals
from aibi.core.schema.output import (
    GUARD_MESSAGE,
    Boundary,
    Output,
    Segment,
    TextSegment,
    admitted_at,
    data,
    dumped_in_validation,
    text,
    unpickled,
)
from aibi.core.schema.pack_api import ImportNote
from aibi.core.schema.refusals import Refusal, RefusalCode

N = "IGNORE_PREVIOUS_INSTRUCTIONS"
GUARD = "server_text"


def _guarded(error: ValidationError) -> bool:
    """Whether the guard itself refused, with its own message."""
    return any(found["type"] == GUARD and found["msg"] == GUARD_MESSAGE for found in error.errors())


def _refusal(message: Any) -> Refusal:
    return Refusal(code=RefusalCode.INVALID_VALUE, path=None, message=message)


class _Mapping(Mapping[str, Any]):
    def __init__(self, given: dict[str, Any]) -> None:
        self._given = given

    def __getitem__(self, key: str) -> Any:
        return self._given[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._given)

    def __len__(self) -> int:
        return len(self._given)


@dataclasses.dataclass
class _Held:
    text: str


class _Foreign(BaseModel):
    text: str


class _Said(Output):
    """Another output with a ``text`` member: what an allow-list by ``Output`` would admit."""

    text: str


class _Floaty(Output):
    """An output whose dump raises when it holds a non-finite number (``check_values``)."""

    value: float


# --- the generated set --------------------------------------------------------------------------

CORE_SET = generated_set.generated_set(aibi.core)
CORE_SET_SIZE = 79
"""The types of the core that hold server text, ``TextSegment`` left out (D399)."""
MODELS = sorted(
    (kind for kind in CORE_SET.values() if issubclass(kind, BaseModel)),
    key=lambda kind: f"{kind.__module__}.{kind.__qualname__}",
)

NOT_WALKED: dict[str, str] = {
    "aibi.core.analyses.packs.PackFailed": "an exception whose message is plain_text of segments",
    "aibi.core.catalog.service.ToolRefused": "an exception holding validated refusals",
    "aibi.core.engine.worker.QueryRefused": "an exception holding a validated refusal",
    "aibi.core.importers.errors.ImportRefused": "an exception holding validated refusals",
    "aibi.core.importers.urls.UrlError": "an exception holding a message of segments",
    "aibi.core.operator.client.Refused": "an exception holding refusals the CLI validated",
    "aibi.core.schema.pack_api.Refused": "a pack's exception; its refusals are read as instances",
    "aibi.core.store.build.BuildRefused": "an exception holding validated refusals",
    "aibi.core.store.carry.CarryRefused": "an exception holding a message of segments",
    "aibi.core.store.edits.EditRefused": "an exception holding a message of segments",
    "aibi.core.store.parquet.UnreadableParquetError": "an exception holding a message of segments",
    "aibi.core.store.sources.SourceError": "an exception holding a message of segments",
    "aibi.core.store.store.StoreRefused": "an exception holding a validated refusal",
    "aibi.core.store.tables.TableError": "an exception holding a message of segments",
    "aibi.core.engine.evaluate.Evaluator": "takes canonical cohorts to evaluate; writes nothing",
    "aibi.core.engine.sql._Compiler": "takes canonical cohorts to compile; writes nothing",
    "aibi.core.engine.variables._Reader": "takes canonical variables to read; writes nothing",
    "aibi.core.schema.params._Walker": "walks a document's parameters; writes nothing",
    "aibi.core.schema.pack_api.Importer": "a pack's protocol; what it returns is re-validated",
    "aibi.core.schema.pack_api.Validator": "a pack's protocol; what it returns is re-validated",
    "aibi.core.schema.pack_api.LeafKind": "a pack's protocol; summary() is re-validated",
    "aibi.core.schema.pack_api.Translator": "a pack's protocol; what it returns is re-validated",
}
"""Plain classes that carry a type of the set (exceptions, which hold what a model validated,
and reach a response only inside an output, which validates it again) and protocols that return
one (a pack's, whose return the core validates again as instances): not walked, each with why.
A ``Callable`` is a value, never walked."""


def test_the_generated_set_is_the_checked_in_size() -> None:
    assert len(CORE_SET) == CORE_SET_SIZE
    assert len(MODELS) == 35


def test_every_carrier_and_protocol_left_out_is_named() -> None:
    bearing = {*CORE_SET.values(), TextSegment}
    assert generated_set.carriers(aibi.core, bearing).keys() == NOT_WALKED.keys()


# --- the product ----------------------------------------------------------------------------------


class _Leaf:
    """Where the segment goes."""


def _tags(model: type[BaseModel]) -> dict[str, object]:
    """A model's members of one ``Literal`` value: a discriminated union's tags."""
    found: dict[str, object] = {}
    for name, info in model.model_fields.items():
        if get_origin(info.annotation) is Literal and len(get_args(info.annotation)) == 1:
            found[info.alias or name] = get_args(info.annotation)[0]
    return found


def _shape(annotation: object, seen: frozenset[type] = frozenset()) -> object:
    """A value reaching one ``TextSegment`` slot of ``annotation``: dicts for models, lists for
    sequences, ``_Leaf`` at the slot; ``None`` if none is reached."""
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if annotation is TextSegment:
        return _Leaf()
    if origin is typing.Annotated:
        return _shape(arguments[0], seen)
    if isinstance(annotation, typing.TypeAliasType):
        return _shape(annotation.__value__, seen)
    if origin in (typing.Union, types.UnionType):
        if TextSegment in arguments:
            return _Leaf()
        for argument in arguments:
            found = _shape(argument, seen)
            if found is not None:
                return found
        return None
    if origin in (list, set, frozenset, collections.abc.Sequence, collections.abc.Iterable) or (
        origin is tuple and len(arguments) == 2 and arguments[1] is Ellipsis
    ):
        found = _shape(arguments[0], seen) if arguments else None
        return None if found is None else [found]
    if origin in (dict, collections.abc.Mapping) and len(arguments) == 2:
        found = _shape(arguments[1], seen)
        return None if found is None else {"k": found}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen or annotation not in {*CORE_SET.values(), TextSegment}:
            return None
        for name, info in annotation.model_fields.items():
            found = _shape(info.annotation, seen | {annotation})
            if found is not None:
                return {**_tags(annotation), info.alias or name: found}
    return None


def _render(shape: object, leaf: Callable[[], object]) -> object:
    if isinstance(shape, _Leaf):
        return leaf()
    if isinstance(shape, list):
        return [_render(item, leaf) for item in cast(list[object], shape)]
    if isinstance(shape, dict):
        return {key: _render(value, leaf) for key, value in cast(dict[str, object], shape).items()}
    return shape


LEAVES: dict[str, Callable[[], object]] = {
    "dict": lambda: {"text": N},
    "OrderedDict": lambda: collections.OrderedDict(text=N),
    "MappingProxyType": lambda: types.MappingProxyType({"text": N}),
    "a Mapping": lambda: _Mapping({"text": N}),
    "SimpleNamespace": lambda: types.SimpleNamespace(text=N),
    "a dataclass": lambda: _Held(text=N),
    "a foreign model": lambda: _Foreign(text=N),
    "an Output look-alike": lambda: _Said(text=N),
    "a data token's text": lambda: types.SimpleNamespace(text=data(N).data),
}
"""What is at the slot: every kind of input that is not an exact ``TextSegment``."""
OPTIONS: dict[str, dict[str, Any]] = {
    "none": {},
    "strict=False": {"strict": False},
    "from_attributes=True": {"from_attributes": True},
    "a dict context": {"context": {"capability": True}},
    "another object as context": {"context": types.SimpleNamespace(member=Boundary.CLI_CLIENT)},
}


def _calls(model: type[BaseModel], shape: object) -> Iterator[tuple[str, Callable[[], object]]]:
    adapter = TypeAdapter(model)
    for leaf_name, leaf in LEAVES.items():
        value = _render(shape, leaf)
        for option_name, option in OPTIONS.items():
            where = f"{leaf_name}, {option_name}"
            yield f"model_validate: {where}", partial(model.model_validate, value, **option)
            yield f"TypeAdapter: {where}", partial(adapter.validate_python, value, **option)
        if isinstance(value, dict):
            yield f"constructor: {leaf_name}", partial(model, **cast(dict[str, Any], value))
    written = json.dumps(_render(shape, lambda: {"text": N}))
    as_strings = cast(dict[str, Any], _render(shape, lambda: {"text": N}))
    for option_name, option in OPTIONS.items():
        if "from_attributes" in option:
            continue
        yield (
            f"model_validate_json: {option_name}",
            partial(model.model_validate_json, written, **option),
        )
        yield f"TypeAdapter json: {option_name}", partial(adapter.validate_json, written, **option)
        yield (
            f"model_validate_strings: {option_name}",
            partial(model.model_validate_strings, as_strings, **option),
        )
        yield (
            f"TypeAdapter strings: {option_name}",
            partial(adapter.validate_strings, as_strings, **option),
        )


@pytest.mark.parametrize("model", MODELS, ids=[kind.__qualname__ for kind in MODELS])
def test_the_product_every_input_kind_option_and_entry_meets_the_guard(
    model: type[BaseModel],
) -> None:
    shape = _shape(model)
    assert shape is not None, "the slot is reached"
    unguarded: list[str] = []
    for name, call in _calls(model, shape):
        try:
            call()
        except ValidationError as error:
            if not _guarded(error):
                unguarded.append(name)
        else:
            unguarded.append(f"{name}: not refused")
    assert unguarded == []


@pytest.mark.parametrize("model", MODELS, ids=[kind.__qualname__ for kind in MODELS])
def test_the_product_an_exact_instance_at_the_slot_passes_the_guard(
    model: type[BaseModel],
) -> None:
    value = _render(_shape(model), lambda: text("ok"))
    refused: list[ValidationError] = []
    for call in (
        partial(model.model_validate, value),
        partial(TypeAdapter(model).validate_python, value),
    ):
        try:
            call()
        except ValidationError as error:  # the members left out
            refused.append(error)
    assert not any(_guarded(error) for error in refused)


CONTAINERS: dict[str, Callable[[dict[str, Any]], object]] = {
    "OrderedDict": collections.OrderedDict,
    "MappingProxyType": types.MappingProxyType,
    "a Mapping": _Mapping,
    "SimpleNamespace": lambda given: types.SimpleNamespace(**given),
    "a dataclass": lambda given: dataclasses.make_dataclass("D", list(given))(**given),
    "a foreign model": lambda given: cast(Any, create_model)(
        "F", **dict.fromkeys(given, (Any, ...))
    )(**given),
}


def _contained(shape: object, container: Callable[[dict[str, Any]], object]) -> object:
    if isinstance(shape, _Leaf):
        return container({"text": N})
    if isinstance(shape, list):
        return [_contained(item, container) for item in cast(list[object], shape)]
    if isinstance(shape, dict):
        return container(
            {
                key: _contained(value, container)
                for key, value in cast(dict[str, object], shape).items()
            }
        )
    return shape


@pytest.mark.parametrize("model", MODELS, ids=[kind.__qualname__ for kind in MODELS])
def test_the_product_no_container_kind_serves_text(model: type[BaseModel]) -> None:
    """Every level a container of one kind: refused somewhere, never served."""
    shape = _shape(model)
    for container in CONTAINERS.values():
        value = _contained(shape, container)
        for option in OPTIONS.values():
            try:
                made = model.model_validate(value, **option)
            except ValidationError:
                continue
            assert N not in made.model_dump_json()


# --- runtime fixtures ---------------------------------------------------------------------------


def _class_creation() -> object:
    class _Say(TextSegment):  # pyright: ignore[reportGeneralTypeIssues]
        pass

    return _Say


RUNTIME: dict[str, Callable[[str], object]] = {
    # round 1
    "n01 a constructor given Any": lambda n: _refusal(json.loads(json.dumps([{"text": n}]))),
    "n02 a constructor given a cast": lambda n: _refusal(cast(Any, [{"text": n}])),
    "n03 model_copy of a message": lambda n: _refusal([text("x")]).model_copy(
        update={"message": [{"text": n}]}
    ),
    "n04 a dataclass carrier": lambda n: QueueNote(
        kind="dropped",
        subject=None,
        message=list(ImportNote("dropped", None, cast(Any, [{"text": n}])).message),
    ),
    "n05 dataclasses.replace": lambda n: QueueNote(
        kind="dropped",
        subject=None,
        message=list(
            dataclasses.replace(
                ImportNote("dropped", None, [text("x")]), message=cast(Any, [{"text": n}])
            ).message
        ),
    ),
    "n06 __replace__": lambda n: cast(Any, copy.copy(text("x"))).__replace__(text=n),
    "n07 the core validator": lambda n: TextSegment.__pydantic_validator__.validate_python(
        {"text": n}
    ),
    "n08 a subclass found": lambda n: cast(
        Any, next(kind for kind in Output.__subclasses__() if kind.__name__ == "TextSegment")
    )(text=n),
    "n09 get_args": lambda n: _text_arm()(text=n),
    "n14 create_model": lambda n: create_model("Holder", m=(Refusal, ...))(
        m={"code": "INVALID_VALUE", "path": None, "message": [{"text": n}]}
    ),
    "n15 validate_call": lambda n: _validated_call(n),
    "n16 partial": lambda n: partial(Refusal, code=RefusalCode.INVALID_VALUE, path=None)(
        message=cast(Any, [{"text": n}])
    ),
    "n17 a comprehension's dict": lambda n: _refusal([{"te" + "xt": part} for part in (n,)]),
    "n19 a data token relabelled": lambda n: _refusal([{"text": data(n).data}]),
    # round 2
    "f01 from_attributes": lambda n: TextSegment.model_validate(
        types.SimpleNamespace(text=n), from_attributes=True
    ),
    "f02 from_attributes nested": lambda n: Refusal.model_validate(
        types.SimpleNamespace(
            code=RefusalCode.INVALID_VALUE,
            path=None,
            message=[types.SimpleNamespace(text=n)],
            alternatives=[],
            limit=None,
            counts=None,
        ),
        from_attributes=True,
    ),
    "f03 from_attributes of a dataclass": lambda n: TextSegment.model_validate(
        _Held(text=n), from_attributes=True
    ),
    "f04 from_attributes of another model": lambda n: TextSegment.model_validate(
        _Foreign(text=n), from_attributes=True
    ),
    "f05 MappingProxyType": lambda n: TextSegment.model_validate(
        types.MappingProxyType({"text": n})
    ),
    "f06 MappingProxyType, lax": lambda n: TextSegment.model_validate(
        types.MappingProxyType({"text": n}), strict=False
    ),
    "f07 a Mapping, lax": lambda n: TextSegment.model_validate(_Mapping({"text": n}), strict=False),
    "f08 a nested Mapping, lax": lambda n: Refusal.model_validate(
        {"code": "INVALID_VALUE", "path": None, "message": [types.MappingProxyType({"text": n})]},
        strict=False,
    ),
    "f09 OrderedDict": lambda n: TextSegment.model_validate(collections.OrderedDict(text=n)),
    "f10 validate_json": lambda n: TextSegment.model_validate_json(json.dumps({"text": n})),
    "f11 validate_strings": lambda n: TextSegment.model_validate_strings({"text": n}),
    "f12 a TypeAdapter's json": lambda n: TypeAdapter(Segment).validate_json(
        json.dumps({"text": n})
    ),
    "f13 a parent's json": lambda n: Refusal.model_validate_json(
        json.dumps({"code": "INVALID_VALUE", "path": None, "message": [{"text": n}]})
    ),
    "f14 a parent built unchecked, served": lambda n: Refusals(
        refusals=[
            Refusal.model_construct(
                code=RefusalCode.INVALID_VALUE, path=None, message=[{"text": n}]
            )
        ]
    ),
    "f20 a thread": lambda n: _in_thread(lambda: _refusal([{"text": n}])),
    "f25 both keys": lambda n: TypeAdapter(Segment).validate_python({"data": n, "text": n}),
    "f27 a dict context": lambda n: TextSegment.model_validate(
        {"text": n}, context={"boundary": True}, strict=False
    ),
    "f29 object.__new__ and __init__": lambda n: object.__new__(TextSegment).__init__(text=n),
    "f30 BaseModel.__init__": lambda n: BaseModel.__init__(
        TextSegment.__new__(TextSegment), text=n
    ),
    "f31 the validator with self_instance": lambda n: (
        TextSegment.__pydantic_validator__.validate_python(
            {"text": n}, self_instance=TextSegment.__new__(TextSegment)
        )
    ),
    "f32 a parent's model_copy": lambda n: _refusal([text("x")]).model_copy(
        update={"message": [types.SimpleNamespace(text=n)]}
    ),
    "f33 a data token by attributes": lambda n: TextSegment.model_validate(
        types.SimpleNamespace(text=data(n).data), from_attributes=True
    ),
    "f34 a TypeAdapter configured from_attributes": lambda n: TypeAdapter(
        list[Segment], config=ConfigDict(from_attributes=True)
    ).validate_python([types.SimpleNamespace(text=n)]),
    "f35 RootModel": lambda n: RootModel[list[Segment]].model_validate([{"text": n}]),
    # round 3
    "B22 type(x)(...)": lambda n: type(text("x"))(text=n),
    "B23 a parent of a dict": lambda n: _refusal([{"text": n}]),
    "B24 model_copy with update": lambda n: text("x").model_copy(update={"text": n}),
    "B28 a TypeAdapter's json": lambda n: TypeAdapter(Segment).validate_json(
        json.dumps({"text": n})
    ),
    "B29 validate_json": lambda n: TextSegment.model_validate_json(json.dumps({"text": n})),
    "B09 model_validate": lambda n: Refusal.model_validate(
        {"code": "INVALID_VALUE", "path": None, "message": [{"text": n}]}
    ),
    "g11 __replace__": lambda n: cast(Any, text("x")).__replace__(text=n),
    "g15 a data token's instance": lambda n: TextSegment.model_validate(data(n)),
    "g16 text and truncated": lambda n: TypeAdapter(Segment).validate_python(
        {"text": n, "truncated": True}, strict=False
    ),
    "g20 an Annotated BeforeValidator": lambda n: create_model(
        "M",
        message=(typing.Annotated[list[Segment], BeforeValidator(lambda value: value)], ...),
    )(message=[{"text": n}]),
    "an Output look-alike by attributes (M03)": lambda n: TextSegment.model_validate(
        _Said(text=n), from_attributes=True
    ),
    "an Output look-alike in a parent": lambda n: _refusal([_Said(text=n)]),
}
"""Each route that builds a ``TextSegment`` some other way than ``text()``, an exact instance or
a boundary: refused by the guard, with its own message."""


def _text_arm() -> Callable[..., object]:
    """``TextSegment`` as the annotation of a message gives it: the tagged union's first arm."""
    tagged = get_args(Refusal.model_fields["message"].annotation)[0]  # Annotated[union, ...]
    [first, _] = get_args(get_args(tagged)[0])  # the union's two arms, each Annotated[kind, Tag]
    return cast(Callable[..., object], get_args(first)[0])


def _validated_call(n: str) -> object:
    @validate_call
    def said(message: list[Segment]) -> list[Segment]:
        return message

    return said(cast(Any, [{"text": n}]))


def _in_thread[T](call: Callable[[], T]) -> T:
    found: list[T | BaseException] = []

    def run() -> None:
        try:
            found.append(call())
        except BaseException as error:  # handed back to the test
            found.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if isinstance(found[0], BaseException):
        raise found[0]
    return cast(T, found[0])


@pytest.mark.parametrize("route", list(RUNTIME.values()), ids=list(RUNTIME))
def test_each_runtime_fixture_is_refused_by_the_guard(route: Callable[[str], object]) -> None:
    with pytest.raises(ValidationError) as raised:
        route(N)
    assert _guarded(raised.value)


def test_a_subclass_cannot_be_created() -> None:
    with pytest.raises(TypeError, match="no subclasses"):
        _class_creation()
    with pytest.raises(TypeError, match="no subclasses"):
        type("Said", (TextSegment,), {"__module__": __name__})
    with pytest.raises(TypeError, match="no subclasses"):
        cast(Any, type(TextSegment)).__new__(
            type(TextSegment), "Said", (TextSegment,), {"__module__": __name__}
        )


def test_model_construct_is_refused() -> None:
    with pytest.raises(TypeError, match="never built unchecked"):
        TextSegment.model_construct(text=N)


class _Plain(Output):
    n: int


V1 = {
    "copy": lambda made: made.copy(update={"text": N}),
    "copy without update": lambda made: made.copy(),
    "copy with a mapping": lambda made: made.copy(**{"update": {"text": N}}),
    "_copy_and_set_values": lambda made: made._copy_and_set_values({"text": N}, set(), deep=False),
    "_iter": lambda made: list(made._iter()),
    "_calculate_keys": lambda made: made._calculate_keys(None, None, False),
    "_get_value": lambda made: type(made)._get_value(made, to_dict=False),
    "construct": lambda made: type(made).construct(text=N),
    "from_orm": lambda made: type(made).from_orm(made),
    "parse_obj": lambda made: type(made).parse_obj({"text": N}),
    "parse_raw": lambda made: type(made).parse_raw('{"text": "x"}'),
    "parse_file": lambda made: type(made).parse_file("x.json"),
    "validate": lambda made: type(made).validate({"text": N}),
    "update_forward_refs": lambda made: type(made).update_forward_refs(),
}
"""Pydantic 1's API that builds, copies or reaches the internals of an output, each called on
an instance of the kind it is given, with the text a look-alike would have."""


@pytest.mark.parametrize("route", list(V1.values()), ids=list(V1))
@pytest.mark.parametrize("kind", [TextSegment, Refusal, _Plain], ids=lambda kind: kind.__name__)
def test_pydantic_1_s_api_is_closed_on_every_output(
    kind: type[Output], route: Callable[[Any], object]
) -> None:
    """The type refuses it (no warning, no text): not even a copy of one holds a mapping."""
    made = {
        TextSegment: lambda: text("harmless"),
        Refusal: lambda: _refusal([text("harmless")]),
        _Plain: lambda: _Plain(n=1),
    }[kind]()
    with pytest.raises(TypeError, match="Pydantic 1's API is closed"):
        route(made)


def test_pydantic_1_s_dumps_still_read() -> None:
    made = text("harmless")
    with pytest.warns(PydanticDeprecatedSince20):
        assert made.dict() == {"text": "harmless"}  # pyright: ignore[reportDeprecated]
    with pytest.warns(PydanticDeprecatedSince20):
        assert json.loads(made.json()) == {"text": "harmless"}  # pyright: ignore[reportDeprecated]
    assert made.model_copy() == made


def test_unpickling_is_refused_outside_a_boundary() -> None:
    written = pickle.dumps(text("x"))
    with pytest.raises(TypeError, match="unpickled only at a listed boundary"):
        pickle.loads(written)
    state = text("x").__getstate__()
    with pytest.raises(TypeError, match="unpickled only at a listed boundary"):
        text("y").__setstate__(state)


def test_unpickled_restores_and_then_holds_nothing_even_after_an_exception() -> None:
    class Failing:
        def load(self) -> object:
            pickle.loads(pickle.dumps(text("inside")))
            raise RuntimeError("the read failed")

    restored = unpickled(Boundary.READER_ANSWER, pickle.Unpickler(_bytes(text("x"))))
    assert restored == text("x")
    with pytest.raises(RuntimeError, match="the read failed"):
        unpickled(Boundary.READER_ANSWER, Failing())
    with pytest.raises(TypeError, match="unpickled only at a listed boundary"):
        pickle.loads(pickle.dumps(text("x")))


def _bytes(value: object) -> Any:
    import io

    return io.BytesIO(pickle.dumps(value))


def test_outputs_validate_instances_again() -> None:
    """``revalidate_instances="always"``: a parent's instance built unchecked is checked when an
    output holds it (``f14``), so it is pinned."""
    assert Output.model_config.get("revalidate_instances") == "always"
    unchecked = Refusal.model_construct(
        code=RefusalCode.INVALID_VALUE, path=None, message=[{"text": N}]
    )
    with pytest.raises(ValidationError) as raised:
        Refusals(refusals=[unchecked])
    assert _guarded(raised.value)


def test_frozen_text_is_not_assigned() -> None:
    segment = text("x")
    with pytest.raises(ValidationError, match="frozen"):
        segment.text = N  # pyright: ignore[reportAttributeAccessIssue]


CONTROLS: dict[str, Callable[[str], object]] = {
    "f18 deepcopy of server text": lambda n: copy.deepcopy(text(n)),
    "g10 copy of server text": lambda n: copy.copy(text(n)),
    "an exact instance in a parent": lambda n: _refusal([text(n)]),
    "an exact instance re-validated": lambda n: TextSegment.model_validate(text(n)),
    "an exact instance by attributes": lambda n: TextSegment.model_validate(
        text(n), from_attributes=True
    ),
}


@pytest.mark.parametrize("route", list(CONTROLS.values()), ids=list(CONTROLS))
def test_server_text_passes_as_itself(route: Callable[[str], object]) -> None:
    assert N in json.dumps(cast(BaseModel, route(N)).model_dump(mode="json"))


@pytest.mark.parametrize(
    "given",
    [
        pytest.param({"data": N}, id="f24 a data dict"),
        pytest.param(data(N), id="g14 a data instance"),
        pytest.param(types.SimpleNamespace(data=N), id="a data look-alike by attributes"),
    ],
)
def test_data_stays_data(given: object) -> None:
    made = TypeAdapter(Segment).validate_python(given, from_attributes=True)
    assert made == data(N)


A2B: dict[str, str] = {
    "n10 importlib's text()": "an alias of text(): text()'s argument is M4.0f-A2b's proof",
    "n11 sys.modules' text()": "an alias of text()",
    "B05-B08 text() under another name": "aliases of text() (partial, map, a lambda)",
    "B20 text() through a function": "a str-to-text funnel",
    "B27 getattr(output, ...) calling text()": "an alias of text()",
    "f26 a str subclass given to text()": "text()'s argument",
}
"""Routes that reach ``text()`` itself: its argument is what M4.0f-A2b proves; A1's census lists
the sites until then."""


@pytest.mark.parametrize("why", list(A2B.values()), ids=list(A2B))
def test_aliases_of_text_are_a2b_s(why: str) -> None:
    pytest.skip(f"M4.0f-A2b: {why}")


# --- the capability is one call's context -------------------------------------------------------


class _Reentrant(Output):
    """A values model whose validator validates a mapping of its own, as g25's did."""

    message: list[Segment]

    @model_validator(mode="before")
    @classmethod
    def _inner(cls, value: object) -> object:
        _refusal([{"text": N}])
        return value


def test_a_validator_reached_from_a_boundary_has_no_capability_of_its_own() -> None:
    written = json.dumps({"message": [{"text": "a"}]})
    with pytest.raises(ValidationError) as raised:
        _Reentrant.model_validate_json(written, context=admitted_at(Boundary.RESULT_CACHE))
    assert _guarded(raised.value)


def test_what_a_boundary_call_s_argument_runs_has_no_capability() -> None:
    """g22-g24: a property, ``__getitem__`` or a module's ``__getattr__`` evaluated for the call
    runs before it, outside its context."""

    class Holder:
        @property
        def payload(self) -> str:
            _refusal([{"text": N}])
            return json.dumps({"code": "INVALID_VALUE", "path": None, "message": []})

    with pytest.raises(ValidationError) as raised:
        Refusal.model_validate_json(Holder().payload, context=admitted_at(Boundary.CLI_CLIENT))
    assert _guarded(raised.value)


def test_no_thread_task_or_context_holds_the_capability() -> None:
    """f20-f23: there is nothing held to inherit, copy or leak."""
    seen: list[str] = []

    class Spawning(Output):
        message: list[Segment]

        @model_validator(mode="after")
        def _spawn(self) -> "Spawning":
            copied = contextvars.copy_context()
            for attempt in (
                lambda: _in_thread(lambda: _refusal([{"text": N}])),
                lambda: copied.run(lambda: _refusal([{"text": N}])),
                lambda: asyncio.run(_task()),
            ):
                try:
                    attempt()
                    seen.append("admitted")
                except ValidationError as error:
                    seen.append("refused" if _guarded(error) else "other")
            return self

    async def _task() -> object:
        await asyncio.sleep(0)
        return _refusal([{"text": N}])

    Spawning.model_validate_json(
        json.dumps({"message": [{"text": "a"}]}), context=admitted_at(Boundary.RESULT_CACHE)
    )
    assert seen == ["refused"] * 3


def test_a_forged_capability_is_refused() -> None:
    for context in (
        {"member": Boundary.CLI_CLIENT},
        Boundary.CLI_CLIENT,
        types.SimpleNamespace(member=Boundary.CLI_CLIENT),
        output,
    ):
        with pytest.raises(ValidationError) as raised:
            TextSegment.model_validate({"text": N}, context=context)
        assert _guarded(raised.value)


def test_each_boundary_s_capability_admits_a_mapping() -> None:
    for member in Boundary:
        made = TextSegment.model_validate({"text": "a"}, context=admitted_at(member))
        assert made == text("a")
    assert repr(admitted_at(Boundary.CLI_CLIENT)) == "<admitted>"
    assert set(output.STORED) == {Boundary.REPORT_NOTES, Boundary.RESULT_CACHE}


def test_a_refused_segment_s_message_names_nothing_it_was_given() -> None:
    with pytest.raises(ValidationError) as raised:
        TextSegment.model_validate({"text": N})
    [error] = raised.value.errors(include_input=False, include_url=False)
    assert N not in json.dumps(error, default=str)
    assert error["msg"] == GUARD_MESSAGE


def test_a_refused_segment_s_error_string_holds_no_input() -> None:
    """``str`` and ``repr`` of the error, which a log of a failure holds, carry no input."""
    with pytest.raises(ValidationError) as raised:
        TextSegment.model_validate({"text": N})
    assert N not in str(raised.value)
    assert N not in repr(raised.value)
    with pytest.raises(ValidationError) as another:
        TextSegment.model_validate_json(json.dumps({"text": N}))
    assert N not in str(another.value)


TAGS = {
    "a text instance": (lambda: text("x"), "text"),
    "a data instance": (lambda: data("x"), "data"),
    "a data mapping": (lambda: {"data": "x"}, "data"),
    "a text mapping": (lambda: {"text": "x"}, "text"),
    "both keys": (lambda: {"data": "x", "text": "y"}, "text"),
    "a mapping of neither": (lambda: {"other": "x"}, "data"),
    "a Mapping that is not a dict": (lambda: _Mapping({"text": "x"}), "text"),
    "an object with a text attribute": (lambda: types.SimpleNamespace(text="x"), "text"),
    "an object with a data attribute": (lambda: types.SimpleNamespace(data="x"), "data"),
    "an Output look-alike": (lambda: _Said(text="x"), "text"),
    "a string": (lambda: "x", "data"),
}
"""Which arm of ``Segment`` takes each input, so that data is not tried against the text guard
first (the cost of re-validating a message of data tokens) and what is not data meets the guard."""


@pytest.mark.parametrize(("made", "arm"), list(TAGS.values()), ids=list(TAGS))
def test_the_segment_union_picks_its_arm_by_what_the_input_is(
    made: Callable[[], object], arm: str
) -> None:
    assert output._segment_tag(made()) == arm  # pyright: ignore[reportPrivateUsage]


def test_the_segment_union_is_written_as_any_of_in_the_schema() -> None:
    """The tag changes no schema a client reads: ``anyOf`` of the two classes, as before."""
    schema = TypeAdapter(Segment).json_schema()
    assert "oneOf" not in schema
    assert schema["anyOf"] == [
        {"$ref": "#/$defs/TextSegment"},
        {"$ref": "#/$defs/DataSegment"},
    ]


def test_what_is_not_a_segment_is_refused_with_the_guard_s_message_or_a_data_error() -> None:
    with pytest.raises(ValidationError) as raised:
        TypeAdapter(Segment).validate_python({"text": N})
    assert _guarded(raised.value)
    with pytest.raises(ValidationError) as other:
        TypeAdapter(Segment).validate_python({"other": 1})
    assert not _guarded(other.value)


# --- a mapping put in place after construction ------------------------------------------------


@dataclasses.dataclass
class _Built:
    """An instance of a model with one segment at every slot, and the mutable containers holding
    them: each with the shape of what it holds, so that a mapping of that shape can replace it."""

    root: Output
    owners: list[Output]
    containers: list[tuple[object, object]]


def _make(annotation: object, seen: frozenset[type], built: _Built) -> object | None:
    """A value for ``annotation`` that holds a segment at each slot reached, the other required
    members of its models ``None`` (a dump refuses them: the guard is the only refusal looked
    for); ``None`` if no slot is reached."""
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if annotation is TextSegment:
        return text("ok")
    if origin is typing.Annotated:
        return _make(arguments[0], seen, built)
    if isinstance(annotation, typing.TypeAliasType):
        return _make(annotation.__value__, seen, built)
    if origin in (typing.Union, types.UnionType):
        if TextSegment in arguments:
            return text("ok")
        for argument in arguments:
            found = _make(argument, seen, built)
            if found is not None:
                return found
        return None
    sequence = origin in (list, collections.abc.Sequence, collections.abc.Iterable)
    if sequence or (origin is tuple and len(arguments) == 2 and arguments[1] is Ellipsis):
        inner = _make(arguments[0], seen, built) if arguments else None
        if inner is None:
            return None
        if not sequence:
            return (inner,)
        held = [inner]
        built.containers.append((held, _shape(arguments[0])))
        return held
    if origin in (dict, collections.abc.Mapping) and len(arguments) == 2:
        inner = _make(arguments[1], seen, built)
        if inner is None:
            return None
        held_by_key = {"k": inner}
        built.containers.append((held_by_key, _shape(arguments[1])))
        return held_by_key
    if isinstance(annotation, type) and issubclass(annotation, Output):
        if annotation in seen or annotation not in {*CORE_SET.values(), TextSegment}:
            return None
        members: dict[str, object] = {}
        for name, info in annotation.model_fields.items():
            found = _make(info.annotation, seen | {annotation}, built)
            if found is not None:
                members[name] = found
            elif info.is_required():
                tag = _tags(annotation).get(info.alias or name)
                members[name] = tag  # a union's tag, so that its arm is the one validated
        if not any(value is not None for value in members.values()):
            return None
        made = cast(Any, annotation).model_construct(**members)
        built.owners.append(made)
        return made
    return None


def _built(model: type[Output]) -> _Built:
    built = _Built(root=text("unset"), owners=[], containers=[])
    made = _make(model, frozenset(), built)
    assert isinstance(made, Output)
    built.root = made
    return built


def _reaches(found: object, target: object, seen: set[int] | None = None) -> bool:
    """Whether ``target`` is held, at any depth, by the output, list or dict ``found``."""
    seen = set() if seen is None else seen
    if found is target:
        return True
    if id(found) in seen:
        return False
    seen.add(id(found))
    if isinstance(found, Output):
        below = list(found.__dict__.values())
    elif isinstance(found, list):
        below = cast(list[object], found)
    elif isinstance(found, dict):
        below = list(cast(dict[str, object], found).values())
    else:
        return False
    return any(_reaches(item, target, seen) for item in below)


def _no_warning[T](call: Callable[[], T]) -> Callable[[], T]:
    def quiet() -> T:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # Pydantic 1's API warns of its deprecation
            return call()

    return quiet


def _deepcopied(held: Output) -> Output:
    try:
        return copy.deepcopy(held)
    except TypeError:  # a held object that cannot be copied (a mapping proxy)
        return held


def _entries(held: Output) -> dict[str, Callable[[], object]]:
    """Every way Pydantic has to dump an output."""
    kind = type(held)
    adapter = TypeAdapter(kind)
    return {
        "model_dump": lambda: held.model_dump(),
        "model_dump json": lambda: held.model_dump(mode="json"),
        "model_dump options": lambda: held.model_dump(
            exclude_none=True, exclude_unset=True, warnings=False, round_trip=True
        ),
        "model_dump_json": lambda: held.model_dump_json(),
        "model_dump_json options": lambda: held.model_dump_json(warnings=False, indent=2),
        "dict": _no_warning(lambda: held.dict()),
        "json": _no_warning(lambda: held.json()),
        "TypeAdapter.dump_python": lambda: adapter.dump_python(held),
        "TypeAdapter.dump_json": lambda: adapter.dump_json(held),
        "pydantic_core.to_json": lambda: pydantic_core.to_json(held),
        "pydantic_core.to_jsonable_python": lambda: pydantic_core.to_jsonable_python(held),
        "model_copy then dump": lambda: held.model_copy().model_dump_json(),
        "copy.copy then dump": lambda: copy.copy(held).model_dump_json(),
        "deepcopy then dump": lambda: _deepcopied(held).model_dump_json(),
    }


JSON_ONLY = "Outputs hold only"


def _refused_by_the_guard(error: BaseException, plain: bool) -> bool:
    """The guard's refusal, from the dump itself or the serialiser's wrap of it, which holds
    nothing of what was refused. What is not a JSON value (a ``dict`` subclass, an object) is
    refused before it, by the check of values; a plain mapping is the guard's."""
    if N in str(error):
        return False
    if isinstance(error, ValidationError):
        return _guarded(error)
    if type(error).__name__ != "PydanticSerializationError":
        return isinstance(error, output.OutputError) and not plain
    return GUARD_MESSAGE in str(error) or (not plain and JSON_ONLY in str(error))


def _refuses(entry: Callable[[], object], plain: bool = True) -> bool | None:
    """Whether the entry was refused as it should be; ``None`` if it wrote."""
    try:
        entry()
    except BaseException as error:
        return _refused_by_the_guard(error, plain)
    return None


LIST_PUTS: dict[str, Callable[[list[Any], object], object]] = {
    "append": lambda held, new: held.append(new),
    "insert": lambda held, new: held.insert(0, new),
    "setitem": lambda held, new: held.__setitem__(0, new),
    "slice assignment": lambda held, new: held.__setitem__(slice(0, 1), [new]),
    "empty slice assignment": lambda held, new: held.__setitem__(slice(0, 0), [new]),
    "extend": lambda held, new: held.extend([new]),
    "in-place add": lambda held, new: held.__iadd__([new]),
}
DICT_PUTS: dict[str, Callable[[dict[str, Any], object], object]] = {
    "replace": lambda held, new: held.__setitem__("k", new),
    "new key": lambda held, new: held.__setitem__("other", new),
    "update": lambda held, new: held.update({"k": new}),
    "setdefault": lambda held, new: held.setdefault("other", new),
}


def _puts(model: type[Output]) -> Iterator[tuple[str, int, bool, Callable[[_Built], object]]]:
    """Each way of putting a mapping, or a look-alike, in place of or beside a segment, at each
    mutable container of a model's instance: ``(what, which container, whether a plain
    mapping, how)``."""
    for index, (held, shape) in enumerate(_built(model).containers):
        puts = LIST_PUTS if isinstance(held, list) else DICT_PUTS
        for put_name, put in puts.items():
            yield (
                f"{put_name} of a mapping to container {index}",
                index,
                True,
                lambda built, put=put, shape=shape, index=index: put(
                    cast(Any, built.containers[index][0]), _render(shape, lambda: {"text": N})
                ),
            )
        first = next(iter(puts.values()))
        for leaf_name, leaf in LEAVES.items():
            yield (
                f"{leaf_name} to container {index}",
                index,
                leaf_name == "dict",
                lambda built, first=first, shape=shape, index=index, leaf=leaf: first(
                    cast(Any, built.containers[index][0]), _render(shape, leaf)
                ),
            )


@pytest.mark.parametrize("model", MODELS, ids=[kind.__qualname__ for kind in MODELS])
def test_class_a_mapping_put_in_place_is_refused_by_every_dump_entry(model: type[Output]) -> None:
    """A frozen output's list is mutable, and a mapping put in one after construction would be
    written as server text: every entry that dumps refuses it, with the guard's own message and
    nothing it was given, whichever instance is dumped (the root or one below it)."""
    clean = _built(model)
    assert clean.containers, "a mutable container holds a segment slot of the model"
    for entry in _entries(clean.root).values():
        assert _refuses(entry) is not True, "the clean instance meets no guard"
    written: list[str] = []
    for name, index, plain, put in _puts(model):
        built = _built(model)
        put(built)
        mutated = built.containers[index][0]
        holders = [o for o in built.owners if o is not built.root and _reaches(o, mutated)]
        for target_name, held in [("root", built.root), *(("owner", o) for o in holders)]:
            entries = _entries(held)
            if target_name == "owner":
                entries = {k: entries[k] for k in ("model_dump", "model_dump_json")}
            for entry_name, entry in entries.items():
                if not _refuses(entry, plain):
                    written.append(f"{name}: {target_name}: {entry_name}")
    assert written == []


def test_the_example_of_review_two_is_refused_by_every_entry() -> None:
    refusal = _refusal([text("h")])
    refusal.message.append(json.loads(json.dumps({"text": N})))
    for entry_name, entry in _entries(refusal).items():
        assert _refuses(entry), entry_name
    refusal.message.clear()
    refusal.message.append(text("h"))
    assert N not in refusal.model_dump_json()


def test_a_parent_that_holds_a_mutated_carrier_refuses_too() -> None:
    refusal = _refusal([text("h")])
    parent = Refusals(refusals=[refusal])  # validated again: the parent holds a copy
    parent.refusals[0].message[0] = cast(Any, {"text": N})
    for entry_name, entry in _entries(parent).items():
        assert _refuses(entry), entry_name


def test_a_nested_guard_refusal_holds_no_input_in_its_error_string() -> None:
    """``hide_input_in_errors`` is on every output, not only on a segment: the error of a
    refusal, a list of refusals or a segment inside either holds none of what it was given. (A
    ``TypeAdapter`` of a list takes the configuration it is given, not its models': each of
    the core's is tested below.)"""
    mapping = {"text": N}
    attempts: dict[str, Callable[[], object]] = {
        "Refusal.model_validate": lambda: Refusal.model_validate(
            {"code": "INVALID_VALUE", "path": None, "message": [mapping]}
        ),
        "Refusals.model_validate_json": lambda: Refusals.model_validate_json(
            json.dumps(
                {"refusals": [{"code": "INVALID_VALUE", "path": None, "message": [mapping]}]}
            )
        ),
        "an extra key": lambda: Refusal.model_validate(
            {"code": "INVALID_VALUE", "path": None, "message": [], "extra": N}
        ),
    }
    for name, attempt in attempts.items():
        with pytest.raises(ValidationError) as raised:
            attempt()
        assert N not in str(raised.value), name
        assert N not in repr(raised.value), name
    assert Output.model_config.get("hide_input_in_errors") is True


def test_only_the_outermost_dump_checks_and_validates_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dump of an output that holds many checks its values and validates them once, not once
    for each output below it (the cost of the dump grows with the size of the output, not with
    its depth)."""
    checked: list[object] = []
    validated: list[type] = []
    real_check = output.check_values
    real_validate = Output.model_validate.__func__  # pyright: ignore[reportFunctionMemberAccess]

    def check(value: object) -> None:
        checked.append(value)
        real_check(value)

    def validate(cls: type[Output], *args: Any, **kwargs: Any) -> Output:
        validated.append(cls)
        return real_validate(cls, *args, **kwargs)

    parent = Refusals(refusals=[_refusal([text("a"), data("b")]) for _ in range(3)])
    monkeypatch.setattr(output, "check_values", check)
    monkeypatch.setattr(Output, "model_validate", classmethod(validate))
    parent.model_dump_json()
    parent.model_dump()
    assert checked == [parent, parent]
    assert validated == [Refusals, Refusals]


def test_the_helpers_dump_checks_the_values_and_its_flag_is_reset_when_it_raises() -> None:
    """``dumped_in_validation`` skips only the validation of the mutable parts, not
    ``check_values``, and the mark it sets is reset when its dump raises: a dump afterwards, in the
    same context, still refuses a mapping put in a list."""
    with pytest.raises(ValueError, match="no non-finite numbers"):
        dumped_in_validation(_Floaty(value=float("nan")), mode="json")
    refusal = _refusal([text("h")])
    refusal.message.append(cast(Any, {"text": N}))
    for entry_name, entry in _entries(refusal).items():
        assert _refuses(entry), entry_name
    assert dumped_in_validation(_Floaty(value=1.0)) == {"value": 1.0}


NO_SEGMENT: dict[str, str] = {
    "aibi.core.api.page._DATASET_ID": "an id from a URL: no segment",
    "aibi.core.catalog.service._BY": "an operator's name: no segment",
    "aibi.core.engine.build._ADAPTER": "a descriptor read from the catalogue: no segment",
    "aibi.core.engine.resolve._CLAUSES": "clauses of a canonical document: no segment",
    "aibi.core.importers.describe._ADAPTER": "a descriptor the importer built: no segment",
    "aibi.core.operator.auth._BY": "an operator's name: no segment",
    "aibi.core.schema.loading._DESCRIPTOR": "a descriptor from a file: no segment",
    "aibi.core.store.build._DESCRIPTORS": "descriptors read back from a blob: no segment",
    "aibi.core.store.carry._ADAPTER": "a descriptor carried to a release: no segment",
    "aibi.core.store.gate._ADAPTER": "a descriptor at the gate: no segment",
    "aibi.core.store.writes._BY": "an operator's name: no segment",
}
"""The module-level ``TypeAdapter`` of the core whose type reaches no type that holds server text,
each with why it needs no configuration of its own."""


def _adapters() -> dict[str, TypeAdapter[Any]]:
    return {
        f"{module.__name__}.{name}": value
        for module in generated_set.modules(aibi.core)
        for name, value in vars(module).items()
        if isinstance(value, TypeAdapter)
    }


def test_each_type_adapter_that_reaches_server_text_holds_no_input_in_its_errors() -> None:
    """A bare ``TypeAdapter`` takes no model's configuration, which governs the error's string,
    so each one whose type reaches a type of the generated set is given
    ``hide_input_in_errors`` itself (found by reflection); the others are listed, with why."""
    bearing = {*CORE_SET.values(), TextSegment}
    adapters = _adapters()
    reaching = {
        name
        for name, adapter in adapters.items()
        if generated_set.mentions(adapter._type, bearing)  # pyright: ignore[reportPrivateUsage]
    }
    assert reaching >= {
        "aibi.core.store.build._NOTE_SEGMENTS",
        "aibi.core.importers.databases._SEGMENTS",
        "aibi.core.importers.worker._REFUSALS",
    }
    assert sorted(set(adapters) - reaching) == sorted(NO_SEGMENT)
    for name in sorted(reaching):
        for given in ({"text": N}, [{"text": N}], [{"code": "x", "message": [{"text": N}]}]):
            with pytest.raises(ValidationError) as raised:
                adapters[name].validate_python(given)
            assert N not in str(raised.value), name
            assert N not in repr(raised.value), name

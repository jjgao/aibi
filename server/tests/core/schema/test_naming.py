"""The schema builders' naming layer (SPEC §12.4, D416): one primitive writes a definition
(``export.put``), the builders' own names are ``RESERVED``, Pydantic's names go through ``Strict``,
and a JSON value is marked by identity, never by name, the marker stripped at every public exit.

The class is tested as a class: a seeded differential property test calls the four public
builders on random model trees whose class names collide with each other, with the reserved
names, ``Substituted*`` and the document's own definitions, and an oracle that does not call the
code under test (unique ``Literal`` marks and enum values, the checked-in schemas for JSON values
and documents) says whether every field is described by its own class. Each layer is also tested
alone, the other patched out (``ISOLATION``). The OpenAPI document's side is
``api/test_openapi_naming.py``.
"""

import ast
import copy
import dataclasses
import enum
import json
import random
import sys
import types
from collections import Counter
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Generic, Literal, TypeVar, Union

import pytest
from pydantic import BaseModel, ConfigDict, Field, RootModel, TypeAdapter, create_model
from pydantic.json_schema import GenerateJsonSchema
from typing_extensions import TypedDict

from aibi.core.analyses.registry import CORE
from aibi.core.catalog.tools import BY_NAME, TOOLS
from aibi.core.mcp import server
from aibi.core.schema import export
from aibi.core.schema.cohorts import RequestDocument
from aibi.core.schema.descriptors import DESCRIPTOR_JSON_MARK, AnyJson, DescModel
from aibi.core.schema.document import DocumentJson
from aibi.core.schema.output import OUTPUT_JSON_MARK, FiniteJsonObject

SCHEMA_DIR = Path(__file__).resolve().parents[4] / "schemas"
EXPORT = Path(export.__file__)
MARK = export.JSON_VALUE_MARK
MODULES = ("aibi_naming_probe_one", "aibi_naming_probe_two")


def checked_in(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    return loaded


@pytest.fixture(autouse=True)
def probe_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two modules the probes' classes claim, so that Pydantic can resolve them."""
    for name in MODULES:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))


# --- RESERVED: one literal, equal to the names the builders define themselves ---------------------


def test_reserved_is_the_names_the_injection_sites_define() -> None:
    """Every ``_define_reserved`` call names a literal, and those literals are ``RESERVED``; the
    JSON values among them are ``json_value``'s, the rest ``ParameterReference``."""
    tree = ast.parse(EXPORT.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_define_reserved"
        ):
            [_, name] = node.args
            assert isinstance(name, ast.Constant), ast.unparse(node)
            names.append(str(name.value))
    assert set(names) == export.RESERVED
    assert export.RESERVED - set(export._JSON_NULL) == {"ParameterReference"}
    assigned = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "RESERVED" for t in node.targets)
    ]
    [reserved] = assigned
    assert isinstance(reserved.value, ast.Call)
    [literal] = reserved.value.args
    assert isinstance(literal, ast.Set)
    assert all(isinstance(element, ast.Constant) for element in literal.elts)


def test_json_value_refuses_a_name_that_is_not_reserved() -> None:
    for name in ("Cohort", "ParameterReference", "Substituted"):
        with pytest.raises(export.SchemaNameError, match="not a reserved JSON value"):
            export.json_value(name)
    assert export.json_value("DocumentJson")["description"] == "Any JSON value but null"
    assert export.json_value("OutputJson")["description"] == "Any JSON value"


# --- The canonical comparison and the primitive ---------------------------------------------------


def test_same_tells_apart_what_python_s_equality_does_not() -> None:
    assert {"default": True} == {"default": 1} == {"default": 1.0}
    assert not export.same({"default": True}, {"default": 1})
    assert not export.same({"default": 1}, {"default": 1.0})
    assert not export.same({"const": -0.0}, {"const": 0.0})
    assert export.same({"a": 1, "b": [1, {"c": 2, "d": 3}]}, {"b": [1, {"d": 3, "c": 2}], "a": 1})
    assert not export.same(2**64, 2**64 + 1)
    assert not export.same(2**53, float(2**53))
    assert export.same(10**30, 10**30)
    assert not export.same({"enum": [1, 2]}, {"enum": [2, 1]})
    with pytest.raises(export.UncomparableError, match="a number that is not finite at"):
        export.same({"default": float("nan")}, {"default": float("nan")})


def test_the_two_markers_are_told_apart() -> None:
    """``plain_marker_loses_null`` (mutant): the marker with ``null`` and the one without are two
    JSON values, and no text either gives is the other's."""
    without, with_null = export._MADE
    assert not export.same(without, with_null)
    assert export.canonical(without) != export.canonical(with_null)
    assert export.same(without, without)
    assert export.same({"a": with_null}, {"a": export._MADE[1]})


SECRET = "secret-value-0451"


class Level(enum.StrEnum):
    a = SECRET


class Rank(enum.IntEnum):
    one = 1


class Text(str):
    __slots__ = ()


class Whole(int):
    __slots__ = ()


def circular_dict() -> Any:
    found: dict[str, Any] = {"x": {"y": []}}
    found["x"]["y"].append(found)
    return found


def circular_list() -> Any:
    found: list[Any] = [1, {"z": 2}]
    found[1]["z"] = found
    return found


REFUSED_VALUES: list[tuple[str, Any, str]] = [
    ("a key of one", {1: SECRET}, r"a key that is not a string at the value$"),
    ("a bool key", {True: SECRET}, r"a key that is not a string at the value$"),
    ("a nested int key", {"a": [{1: SECRET}]}, r"a key that is not a string at \['a'\]\[0\]$"),
    ("a StrEnum key", {Level.a: 1}, r"a key that is not a string at the value$"),
    ("a tuple", {"a": (1, SECRET)}, r"a tuple, which is no JSON, at \['a'\]$"),
    ("a tuple at the root", (1, SECRET), r"a tuple, which is no JSON, at the value$"),
    ("a StrEnum", {"const": Level.a}, r"a Level, which is no JSON, at \['const'\]$"),
    ("an IntEnum", {"const": Rank.one}, r"a Rank, which is no JSON, at \['const'\]$"),
    ("a str subclass", {"const": Text(SECRET)}, r"a Text, which is no JSON, at \['const'\]$"),
    ("an int subclass", {"const": Whole(7)}, r"a Whole, which is no JSON, at \['const'\]$"),
    ("a set", {"enum": {SECRET}}, r"a set, which is no JSON, at \['enum'\]$"),
    ("a bytes", {"enum": [SECRET.encode()]}, r"a bytes, which is no JSON, at \['enum'\]\[0\]$"),
    ("a forged marker", {"x": {"\u0000made": True}}, r"a marker's own key at \['x'\]$"),
    ("a circular dict", circular_dict(), r"a circular structure at \['x'\]\['y'\]\[0\]$"),
    ("a circular list", circular_list(), r"a circular structure at \[1\]\['z'\]$"),
    ("a nan", {"a": [1, float("nan")]}, r"a number that is not finite at \['a'\]\[1\]$"),
    ("an inf", {"a": float("inf")}, r"a number that is not finite at \['a'\]$"),
]


@pytest.mark.parametrize(
    ("label", "value", "why"), REFUSED_VALUES, ids=lambda x: x if isinstance(x, str) else ""
)
def test_canonical_refuses_what_json_would_write_alike_naming_where_never_what(
    label: str, value: Any, why: str
) -> None:
    """``canonical`` equates ``{1: x}`` with ``{"1": x}``, a tuple with a list, a ``StrEnum`` with
    its string and a dict forging a marker's text with the marker: it refuses each instead, and
    a circular structure and a number that is not finite, naming where in the value and never
    what it holds; ``put`` says the same after its key, and (a refusal being the answer to a
    second value) only when the key is taken."""
    with pytest.raises(export.UncomparableError, match=why) as raised:
        export.canonical(value)
    assert SECRET not in str(raised.value)
    with pytest.raises(export.UncomparableError, match=why):
        export.same(value, {"other": 1})
    found: dict[str, Any] = {}
    export.put(found, "K", value, "two of {key}")  # a first value is never compared
    for first, second in ((value, {"other": 1}), ({"other": 1}, value)):
        found = {"K": first}
        with pytest.raises(export.SchemaNameError) as put_raised:
            export.put(found, "K", second, "two of {key}")
        assert str(put_raised.value).startswith("two of K: holds ")
        assert SECRET not in str(put_raised.value)
        assert found == {"K": first}, label


def test_what_canonical_would_equate_is_never_equal_to_its_look_alike() -> None:
    """The five pairs the review found ``canonical`` equating (each refused now)."""
    alikes = [
        ({1: "x"}, {"1": "x"}),
        ((1, 2), [1, 2]),
        ({"const": Level.a}, {"const": SECRET}),
        ({"x": export._MADE[1]}, {"x": {"\u0000made": True}}),
        ({True: 1}, {"true": 1}),
    ]
    for first, second in alikes:
        assert json.dumps(first, sort_keys=True, default=export._plain) == json.dumps(
            second, sort_keys=True, default=export._plain
        ), first
        with pytest.raises(export.UncomparableError):
            export.same(first, second)


def test_put_refuses_another_value_and_names_the_key_never_a_value() -> None:
    found: dict[str, Any] = {}
    export.put(found, "A", {"default": 1}, "two of {key}")
    export.put(found, "A", {"default": 1}, "two of {key}")
    with pytest.raises(export.SchemaNameError, match=r"^two of A$"):
        export.put(found, "A", {"default": True}, "two of {key}")
    assert found == {"A": {"default": 1}}
    export.put(found, "N", {"default": float("nan")}, "two of {key}")
    with pytest.raises(export.SchemaNameError) as raised:
        export.put(found, "N", {"default": float("nan")}, "two of {key}")
    assert str(raised.value) == "two of N: holds a number that is not finite at ['default']"
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__, "the inner error is dropped"


def test_put_compares_what_is_no_json_by_identity() -> None:
    """A route, a partial, a class (the values of the generator's maps that are no JSON) are equal
    to themselves and to nothing else, a dict holding one included (its dict is another)."""
    one, other = object(), object()
    objects: dict[str, object] = {}
    export.put(objects, "x", one, "{key}")
    export.put(objects, "x", one, "{key}")
    with pytest.raises(export.SchemaNameError, match=r"^x$"):
        export.put(objects, "x", other, "{key}")
    kinds: dict[str, Any] = {"p": partial(int, 1), "c": Level}
    export.put(kinds, "p", kinds["p"], "{key}")
    export.put(kinds, "c", Level, "{key}")
    with pytest.raises(export.SchemaNameError, match=r"^p$"):
        export.put(kinds, "p", partial(int, 1), "{key}")
    with pytest.raises(export.SchemaNameError, match=r"^c$"):
        export.put(kinds, "c", Rank, "{key}")
    held: dict[str, Any] = {"d": {"route": one}}
    with pytest.raises(export.SchemaNameError, match=r"^d$"):
        export.put(held, "d", {"route": one}, "{key}")
    shared = {"route": one}
    export.put({"d": shared}, "d", shared, "{key}")


def test_a_circular_structure_is_told_from_a_number_that_is_not_finite() -> None:
    """One ``ValueError`` of ``json`` once said "not finite" for both."""
    loop: dict[str, Any] = {}
    loop["self"] = loop
    for held, new in (({"k": loop}, {"self": 1}), ({"k": {"self": 1}}, loop)):
        with pytest.raises(export.SchemaNameError) as raised:
            export.put(held, "k", new, "two of {key}")
        assert str(raised.value) == "two of k: holds a circular structure at ['self']"
        assert "not finite" not in str(raised.value)
    nan = {"self": float("nan")}
    with pytest.raises(export.SchemaNameError, match="holds a number that is not finite") as other:
        export.put({"k": nan}, "k", {"self": 1}, "two of {key}")
    assert "circular" not in str(other.value)


def test_two_tools_writing_one_file_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tool names ``a_b`` and ``a-b`` would write one file each."""
    model = export.TOOL_MODELS["search_catalog"]
    monkeypatch.setattr(export, "TOOL_MODELS", {"a_b": model, "a-b": model})
    with pytest.raises(export.SchemaNameError, match=r"two tools write tool\.a-b\.request"):
        export._tool_schemas()


def test_tool_names_are_unique_and_the_lookup_holds_every_tool() -> None:
    assert len(BY_NAME) == len(TOOLS) == len({tool.name for tool in TOOLS})


# --- Strict: Pydantic's own merge of two classes of one name -------------------------------------


def two_opts(first: object, second: object) -> type[BaseModel]:
    one = create_model("Opt", __module__=MODULES[0], x=(int | bool | float, first))
    two = create_model("Opt", __module__=MODULES[1], x=(int | bool | float, second))
    return create_model("Holder", __module__=MODULES[0], one=(one, ...), two=(two, ...))


def test_pydantic_merges_two_classes_of_one_name_that_compare_equal() -> None:
    """The case ``Strict`` exists for: Pydantic's ``==`` merge describes a field by another
    class's default (``1`` for ``true``). If an upgrade stops merging, this fails, and ``Strict``'s
    refusal of it can be reviewed again."""
    found = TypeAdapter(two_opts(1, True)).json_schema()
    assert list(found["$defs"]) == ["Opt"]
    assert found["$defs"]["Opt"]["properties"]["x"]["default"] == 1


@pytest.mark.parametrize(("first", "second"), [(1, True), (1, 1.0), (0, False), (0.0, False)])
def test_strict_refuses_two_classes_pydantic_would_merge(first: object, second: object) -> None:
    """Pinned against the private hook it overrides (``_build_definitions_remapping``): a Pydantic
    upgrade that renames or stops calling it fails here."""
    assert callable(getattr(GenerateJsonSchema, "_build_definitions_remapping", None))
    with pytest.raises(export.SchemaNameError, match="two classes Pydantic names Opt"):
        TypeAdapter(two_opts(first, second)).json_schema(schema_generator=export.Strict)
    with pytest.raises(export.SchemaNameError, match="two classes Pydantic names Opt"):
        export.output_schema(two_opts(first, second), "x")


def test_strict_merges_two_classes_whose_schemas_are_one() -> None:
    found = TypeAdapter(two_opts(1, 1)).json_schema(schema_generator=export.Strict)
    assert list(found["$defs"]) == ["Opt"]
    distinct = TypeAdapter(two_opts(1, 2)).json_schema(schema_generator=export.Strict)
    assert sorted(distinct["$defs"]) == [f"{MODULES[0]}__Opt", f"{MODULES[1]}__Opt"]


def nested_twins(first: object, second: object) -> type[BaseModel]:
    """``Outer(i: Inner)`` in each of two modules, ``Inner.x`` ``first`` in one, ``second`` in the
    other, both used by one model."""

    def pair(module: str, value: object) -> type[BaseModel]:
        inner = create_model("Inner", __module__=module, x=(int | bool, value))
        return create_model("Outer", __module__=module, i=(inner, ...))

    return create_model(
        "Holder",
        __module__=MODULES[0],
        a=(pair(MODULES[0], first), ...),
        b=(pair(MODULES[1], second), ...),
    )


def test_strict_merges_nested_twins_whose_schemas_are_one_after_pydantic_s_renaming() -> None:
    """Two modules each defining ``Inner(x=1)`` and ``Outer(i: Inner)``: Pydantic merges both
    pairs, and the two ``Outer`` bodies differ only by the name of the ``Inner`` each refers to
    before that merge; refusing them as "different" said what is untrue."""
    model = nested_twins(1, 1)
    plain = TypeAdapter(model).json_schema()
    assert sorted(plain["$defs"]) == ["Inner", "Outer"]
    found = TypeAdapter(model).json_schema(schema_generator=export.Strict)
    assert found == plain
    built: Any = export.output_schema(model, "x")
    assert sorted(built["$defs"]) == ["Inner", "Outer"]
    assert built["$defs"]["Inner"]["properties"]["x"]["default"] == 1
    # The same bodies under names Pydantic could not merge stay two definitions, accepted.
    apart = TypeAdapter(nested_twins(1, 2)).json_schema(schema_generator=export.Strict)
    assert sorted(apart["$defs"]) == [
        f"{MODULES[0]}__Inner",
        f"{MODULES[0]}__Outer",
        f"{MODULES[1]}__Inner",
        f"{MODULES[1]}__Outer",
    ]
    # Nested twins whose inner schemas differ as JSON (1 and true) are refused, at the inner one.
    with pytest.raises(export.SchemaNameError, match="two classes Pydantic names Inner"):
        TypeAdapter(nested_twins(1, True)).json_schema(schema_generator=export.Strict)


def test_strict_leaves_the_generator_s_own_definitions_as_they_were() -> None:
    """``strict_no_deepcopy`` (mutant): ``remap_json_schema`` rewrites a body in place, so each
    body is remapped as a copy and ``self.definitions`` is what Pydantic gave, unchanged, however
    idempotent Pydantic's own remap is (nested twins in two modules whose bodies are apart, so
    that the references inside them are renamed)."""
    seen: list[tuple[Any, Any]] = []

    class Watching(export.Strict):
        def _build_definitions_remapping(self):  # Pydantic's private return type
            before = copy.deepcopy(self.definitions)
            remapping = super()._build_definitions_remapping()
            seen.append((before, copy.deepcopy(self.definitions)))
            return remapping

    found = TypeAdapter(nested_twins(1, 2)).json_schema(schema_generator=Watching)
    assert len(found["$defs"]) == 4
    [(before, after)] = seen
    assert len(before) == 4
    assert json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True)


def test_strict_refuses_a_number_that_is_not_finite_below_the_root() -> None:
    """``strict_nonfinite_ignored`` (mutant): a definition's nested ``default=inf`` is refused
    by name, never by value (the root's own is the builders' to bound, as before)."""
    inner = create_model("Inner", __module__=MODULES[0], x=(float, float("inf")))
    holder = create_model("Holder", __module__=MODULES[0], i=(inner, ...))
    with pytest.raises(export.SchemaNameError, match=r"^Inner holds a number that is not finite"):
        export.output_schema(holder, "x")
    with pytest.raises(export.SchemaNameError, match="Inner holds a number that is not finite"):
        TypeAdapter(holder).json_schema(schema_generator=export.Strict)


def test_every_generation_of_the_builders_is_strict() -> None:
    """Every Pydantic JSON Schema call in ``export`` passes a ``Strict`` generator, and no other
    way to generate one is there in any form (a call, an import, an alias, a string)."""
    source = EXPORT.read_text(encoding="utf-8")
    assert generation_violations(source) == []
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("json_schema", "model_json_schema")
    ]
    assert len(calls) == 6
    for call in calls:
        given = {word.arg: ast.unparse(word.value) for word in call.keywords}
        assert given.get("schema_generator") in ("Strict", "_Nulls"), ast.unparse(call)
    assert issubclass(export._Nulls, export.Strict)


GENERATING = frozenset({"json_schema", "model_json_schema"})
GENERATORS = frozenset(
    {"models_json_schema", "json_schemas", "generate", "generate_definitions", "generate_inner"}
)
PYDANTIC_V1 = frozenset({"schema", "schema_json"})
READING_ATTRIBUTES = frozenset({"getattr", "__getattribute__", "attrgetter", "getattr_static"})


def generation_violations(source: str) -> list[str]:
    """Every way ``source`` could generate a JSON Schema but a ``.json_schema(...)`` or
    ``.model_json_schema(...)`` call with a ``Strict`` generator: a name, attribute, import or
    string of ``models_json_schema``, ``TypeAdapter.json_schemas`` or a generator's own
    ``generate``, a bare ``json_schema``, an instance of ``GenerateJsonSchema`` itself, a
    ``schema_generator`` that is not ``Strict`` or ``_Nulls``, a reference to either call that is
    no call, Pydantic v1's ``.schema()`` and ``.schema_json()`` (as an attribute or a ``getattr``
    string), and a ``getattr`` whose name is no string literal (``"model_json" + "_schema"``)."""
    tree = ast.parse(source)
    found: list[str] = []
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, ast.Attribute):
            names = [node.attr]
        elif isinstance(node, ast.alias):
            names = node.name.split(".")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names = [node.value]
        for name in names:
            if name in GENERATORS:
                found.append(f"{name} at {getattr(node, 'lineno', '?')}")
            if name in GENERATING and (
                not isinstance(node, ast.Attribute) or id(node) not in called
            ):
                found.append(f"{name} not called at {getattr(node, 'lineno', '?')}")
        if isinstance(node, ast.Attribute) and node.attr in PYDANTIC_V1:
            found.append(f"{node.attr} at {node.lineno}")
        if isinstance(node, ast.Call):
            callee = ast.unparse(node.func)
            if callee.split(".")[-1] in READING_ATTRIBUTES:
                named = node.args[1] if len(node.args) > 1 else None
                if not (isinstance(named, ast.Constant) and isinstance(named.value, str)):
                    found.append(f"{callee} with no literal name at {node.lineno}")
                elif named.value in PYDANTIC_V1:
                    found.append(f"{callee}(…, {named.value!r}) at {node.lineno}")
            if callee.split(".")[-1] == "GenerateJsonSchema":
                found.append(f"{callee}(...) at {node.lineno}")
            if callee.split(".")[-1] in GENERATING:
                given = {word.arg: ast.unparse(word.value) for word in node.keywords}
                if given.get("schema_generator") not in ("Strict", "_Nulls"):
                    found.append(f"{ast.unparse(node)} at {node.lineno}")
    return found


GENERATIONS = {
    "models_json_schema call": "x = models_json_schema([(A, 'validation')])",
    "models_json_schema import": "from pydantic.json_schema import models_json_schema",
    "models_json_schema attribute": "x = pydantic.json_schema.models_json_schema(a)",
    "GenerateJsonSchema().generate": "x = GenerateJsonSchema().generate(s)",
    "a generator's generate": "x = Strict(by_alias=True).generate(s)",
    "generate_definitions": "x = Strict().generate_definitions(a)",
    "TypeAdapter.json_schemas": "x = TypeAdapter.json_schemas(a)",
    "json_schemas attribute": "x = adapter.json_schemas([a])",
    "a bare model_json_schema": "x = model_json_schema(A)",
    "a bare model_json_schema alias": "from pydantic import model_json_schema",
    "an alias of the method": "m = A.model_json_schema\nx = m()",
    "a getattr string": "x = getattr(A, 'model_json_schema')()",
    "a call without a generator": "x = A.model_json_schema()",
    "a call with another generator": "x = adapter.json_schema(schema_generator=GenerateJsonSchema)",
    "a json_schema reference": "x = adapter.json_schema",
    "Pydantic v1's schema": "x = A.schema()",
    "Pydantic v1's schema_json": "x = A.schema_json(indent=2)",
    "a getattr of schema": "x = getattr(A, 'schema')()",
    "a getattr of a concatenated name": "x = getattr(A, 'model_json' + '_schema')()",
    "a getattr of a joined name": "x = getattr(A, ''.join(['model_json', '_schema']))()",
    "a getattr of a formatted name": "x = getattr(A, f'model_{kind}')()",
    "a getattr of a variable": "x = getattr(A, name)()",
    "a getattr of a keyword name": "x = getattr(A, name=n)()",
    "an object.__getattribute__ of a variable": "x = object.__getattribute__(A, name)()",
}


@pytest.mark.parametrize("form", list(GENERATIONS))
def test_the_generation_lint_catches_every_other_way_to_generate(form: str) -> None:
    assert generation_violations(GENERATIONS[form]), form
    assert generation_violations("x = A.model_json_schema(schema_generator=Strict)") == []
    assert generation_violations("x = TypeAdapter(a).json_schema(schema_generator=_Nulls)") == []
    assert generation_violations("x = getattr(A, 'model_dump')(); y = schema['cls']") == []


# --- A class Pydantic names like a reserved name --------------------------------------------------


class Outer:
    class RequestJson(BaseModel):
        shape: int


def test_pydantic_names_a_definition_by_the_class_s_name_at_its_creation() -> None:
    """The pre-check reads Pydantic's keys, not ``__name__``: a nested class is keyed by its own
    name, and a class renamed after its creation keeps its old key."""
    nested = create_model("Probe", __module__=MODULES[0], x=(Outer.RequestJson, ...))
    assert list(TypeAdapter(nested).json_schema()["$defs"]) == ["RequestJson"]
    renamed = create_model("Plain", __module__=MODULES[0], shape=(int, ...))
    renamed.__name__ = renamed.__qualname__ = "RequestJson"
    holder = create_model("Probe", __module__=MODULES[0], x=(renamed, ...))
    assert list(TypeAdapter(holder).json_schema()["$defs"]) == ["Plain"]
    found: Any = export.output_schema(holder, "x")
    assert found["$defs"]["Plain"]["properties"]["shape"]


def reserved_cases() -> Iterator[tuple[str, str, Callable[[str, type[BaseModel]], Any]]]:
    builders: dict[str, Callable[[str, type[BaseModel]], Any]] = {
        "request": lambda name, model: export.request_schema(model, name),
        "output": lambda name, model: export.output_schema(model, name),
        "params": lambda _, model: export.params_schema(model),
        "values": lambda _, model: export.values_schema(model),
    }
    for name in sorted(export.RESERVED):
        for builder, build in builders.items():
            yield name, builder, build


@pytest.mark.parametrize("kind", ["model", "enum"])
@pytest.mark.parametrize("depth", [1, 2])
@pytest.mark.parametrize(("name", "builder", "build"), list(reserved_cases()))
def test_a_class_named_like_a_reserved_name_is_refused(
    name: str, builder: str, build: Callable[[str, type[BaseModel]], Any], kind: str, depth: int
) -> None:
    """The round 3 probes' 16 accepted-and-wrong cases, as a class: a model or an enum named like
    a builder's own definition, nested at depth 1 or 2, beside a JSON value (whose definition it
    would replace) in every builder, is refused, naming the class by its module and name."""
    if kind == "enum":
        inner: Any = enum.Enum(name, {"a": "a", "b": "b"}, module=MODULES[1])
    else:
        inner = create_model(name, __module__=MODULES[1], secret_shape=(int, ...))
    if depth == 2:
        inner = create_model("Middle", __module__=MODULES[0], inner=(inner, ...))
    json_field = AnyJson if builder == "request" and name == "DescriptorJson" else FiniteJsonObject
    model = create_model(
        "Probe", __module__=MODULES[0], values=(json_field, ...), nested=(inner, ...)
    )
    with pytest.raises(export.SchemaNameError, match=f"Pydantic names {name}") as raised:
        build("Probe", model)
    assert f"{MODULES[1]}__{name}" in str(raised.value), builder


def test_the_injected_request_json_probe_is_refused() -> None:
    """``p_injected.py``: a request model whose ``nested`` member is a model named
    ``RequestJson`` beside a JSON value (whose ``RequestJson`` replaced the model's definition)."""
    inner = create_model(
        "RequestJson", __module__=MODULES[0], secret_shape=(int, ...), other=(str, ...)
    )
    probe = create_model(
        "ProbeRequest", __module__=MODULES[0], values=(FiniteJsonObject, ...), nested=(inner, ...)
    )
    with pytest.raises(export.SchemaNameError, match="RequestJson, a name the builders reserve"):
        export.request_schema(probe, "ProbeRequest")


# --- The JSON-value marker: by identity, internal, stripped at every public exit ----------------


def every_public_output() -> Iterator[tuple[str, Any]]:
    for name, build in export.SCHEMAS.items():
        yield name, build()
    for tool in TOOLS:
        definition = server._definition(tool)
        yield f"mcp:{tool.name}", [definition.inputSchema, definition.outputSchema]
    for key, core in CORE.items():
        yield f"core:{key}", core.entry.model_dump(mode="json")


def test_no_public_output_carries_the_marker() -> None:
    """Every ``SCHEMAS`` file, MCP's tool definitions and the core analyses' entries (whose
    ``fields`` hold ``params_schema`` and ``values_schema``): the marker is the builders' alone."""
    seen = 0
    for name, output in every_public_output():
        assert MARK not in json.dumps(output), name
        seen += 1
    assert seen == len(export.SCHEMAS) + len(TOOLS) + len(CORE)


def test_the_marked_builders_carry_the_marker_by_identity() -> None:
    made: dict[str, Any] = {}
    marked: Any = None
    for tool in ("count_cohort", "validate_document", "propose_descriptor"):
        marked = export.request_schema_marked(export.TOOL_MODELS[tool][0], tool)
        defs = marked["$defs"]
        assert isinstance(defs, dict)
        for name, body in defs.items():
            if isinstance(body, dict) and MARK in body:
                made[name] = body[MARK]
    assert sorted(made) == ["DescriptorJson", "DocumentJson", "ParameterValue", "RequestJson"]
    assert [export.made(made[name]) for name in sorted(made)] == [True, False, False, True]
    assert made["DescriptorJson"] is made["RequestJson"]
    with pytest.raises(TypeError, match="_Made is not JSON serializable"):
        json.dumps(marked)  # a forgotten strip fails loudly
    for marker in made.values():
        assert copy.copy(marker) is marker
        assert copy.deepcopy(marker) is marker
        assert "null" not in repr(marker)
    assert copy.deepcopy(marked)["$defs"]["DescriptorJson"][MARK] is made["DescriptorJson"]


def test_a_forged_marker_is_refused() -> None:
    """``p_marker_forge.py``: a model whose own schema carries the marker's key at its top level
    (``json_schema_extra``) is no JSON value: every public builder refuses it."""
    forged = create_model(
        "HealthLike",
        __module__=MODULES[0],
        __config__=ConfigDict(json_schema_extra={MARK: {"null": True}}),
        status=(str, ...),
    )
    holder = create_model("Holder", __module__=MODULES[0], inner=(forged, ...))
    for build in (export.output_schema, export.request_schema):
        for model in (forged, holder):
            with pytest.raises(export.SchemaNameError, match="did not give"):
                build(model, "x")
    with pytest.raises(export.SchemaNameError, match="did not give"):
        export.made({"null": True})


def test_a_member_named_like_the_marker_is_not_taken_for_it() -> None:
    """Only a definition's top level is read: a member whose name is the marker's key is a
    member."""
    model = create_model(
        "Named", __module__=MODULES[0], member=(int, Field(alias=MARK)), other=(str, ...)
    )
    found: Any = export.output_schema(model, "x")
    assert found["properties"][MARK]["type"] == "integer"
    assert "$defs" not in found


def test_a_marker_left_below_a_definition_s_top_level_is_refused() -> None:
    marker = export._json_value("OutputJson")[MARK]
    with pytest.raises(export.SchemaNameError, match="below a definition's top level"):
        export.unmarked(export.MarkedSchema({"properties": {"a": {MARK: marker}}}))
    plain = export.unmarked(export.MarkedSchema({"$defs": {"A": {"type": "string", MARK: marker}}}))
    assert plain == {"$defs": {"A": {"type": "string"}}}


def test_a_marker_left_in_a_list_is_refused() -> None:
    """``unmarked`` scanned the values of dicts alone: a marker that is a list's item, in a
    union's members or an ``enum``, would have reached ``json.dumps`` or a public output."""
    marker = export._json_value("OutputJson")[MARK]
    held: Any
    for held in (
        {"anyOf": [{"type": "string"}, marker]},
        {"properties": {"a": {"items": [[marker]]}}},
        {"enum": [1, [2, {"b": marker}]]},
    ):
        with pytest.raises(export.SchemaNameError, match="below a definition's top level"):
            export.unmarked(export.MarkedSchema({"$defs": {"A": held}}))
        with pytest.raises(export.SchemaNameError, match="below a definition's top level"):
            export.unmarked(export.MarkedSchema(held))


# --- Declared nulls by identity ------------------------------------------------------------------


def test_a_descriptor_model_named_like_a_document_model_leaves_the_descriptor_schema() -> None:
    """``p_declared_nulls2.py``: a descriptor model of another module named like each model of a
    document the descriptor schema reaches (a coverage's ``parent_scope``), declaring every
    member nullable, and one with a ``json_schema_extra`` of its own, change nothing. Under the
    identity-based ``_Nulls`` (nulls read from the class Pydantic generates, never looked up by
    name) this is vacuous by construction: the classes made here are in no schema the builder
    reads, so the byte-identity of the descriptor schema with the checked-in file and the mutant
    ``nulls_by_subclass_name`` (a lookup by the class's name) are what pin it, and this test only
    records the case."""
    before = export.render(export.descriptor_schema())
    assert before == (SCHEMA_DIR / "descriptor.schema.json").read_text(encoding="utf-8")
    defs = checked_in("descriptor.schema.json")["$defs"]
    lenders = [name for name, body in defs.items() if body.get("properties")]
    assert len(lenders) > 40
    made = []
    for name in lenders:
        lender = type(name, (DescModel,), {"__module__": MODULES[1]})
        lender._nullable = frozenset(defs[name]["properties"])
        made.append(lender)
    own_extra = type(
        "Disclosure",
        (DescModel,),
        {
            "__module__": MODULES[1],
            "__annotations__": {"a": int | None},
            "a": None,
            "model_config": ConfigDict(json_schema_extra={"examples": [{}]}),
        },
    )
    own_extra._nullable = frozenset({"a"})
    assert export.render(export.descriptor_schema()) == before
    assert made


def test_the_declared_null_mark_never_leaves_the_descriptor_schema() -> None:
    assert "x-aibi-nullable" not in json.dumps(export.descriptor_schema())
    nullable: Any = export.descriptor_schema()["$defs"]
    assert any(
        "null" in json.dumps(member)
        for body in nullable.values()
        for member in body.get("properties", {}).values()
    )


# --- The keyword-level guards ---------------------------------------------------------------------


def test_a_union_s_one_member_and_its_parent_never_overwrite_a_validation_keyword() -> None:
    """m7: the parent's annotation wins (its ``description``), a validation keyword both give
    differently is refused, and one both give alike is kept."""
    merged = export.without_null(
        {
            "anyOf": [{"$ref": "#/a", "description": "member's"}, {"type": "null"}],
            "description": "parent's",
        }
    )
    assert merged == {"$ref": "#/a", "description": "parent's"}
    with pytest.raises(export.SchemaNameError, match=r"both give \['maxLength'\]"):
        export.without_null(
            {"anyOf": [{"type": "string", "maxLength": 3}, {"type": "null"}], "maxLength": 5}
        )
    alike = export.without_null(
        {"anyOf": [{"type": "string", "maxLength": 3}, {"type": "null"}], "maxLength": 3}
    )
    assert alike == {"type": "string", "maxLength": 3}
    assert "default" in export.ANNOTATIONS
    assert "type" not in export.ANNOTATIONS


def test_a_union_s_member_and_its_parent_are_compared_as_canonical_text() -> None:
    """``merged_python_equality`` (mutant): ``1`` and ``true`` are equal with ``==``, so a
    ``const`` of ``1`` in the member and of ``true`` in the parent was merged, one overwriting
    the other; canonical text tells them apart."""
    with pytest.raises(export.SchemaNameError, match=r"both give \['const'\]"):
        export.without_null({"anyOf": [{"const": 1}, {"type": "null"}], "const": True})
    with pytest.raises(export.SchemaNameError, match=r"both give \['const'\]"):
        export.without_null({"anyOf": [{"const": 1.0}, {"type": "null"}], "const": 1})
    alike = export.without_null({"anyOf": [{"const": 1}, {"type": "null"}], "const": 1})
    assert alike == {"const": 1}


@pytest.mark.parametrize("order", ["allOf first", "oneOf first"])
def test_a_union_beside_an_any_of_joins_the_node_s_own_all_of(order: str) -> None:
    """``_allow_references``: ``oneOf`` beside ``anyOf`` becomes an ``allOf`` conjunct after the
    node's own conjuncts, whatever the keywords' order (one used to overwrite the other)."""
    one_of: Any = [{"const": "a"}]
    any_of: Any = [{"const": "b"}]
    all_of: Any = [{"required": ["x"]}]
    node: dict[str, Any] = (
        {"allOf": all_of, "anyOf": any_of, "oneOf": one_of}
        if order == "allOf first"
        else {"oneOf": one_of, "anyOf": any_of, "allOf": all_of}
    )
    found = export._allow_references(node)
    assert isinstance(found, dict)
    assert found["allOf"] == [{"required": ["x"]}, {"anyOf": [{"const": "a"}]}]
    assert found["anyOf"] == [{"const": "b"}]
    assert export._allow_references({"oneOf": one_of, "anyOf": any_of}) == {
        "allOf": [{"anyOf": one_of}],
        "anyOf": any_of,
    }
    assert export._allow_references({"oneOf": one_of}) == {"anyOf": one_of}


def test_a_marked_json_value_with_a_reference_of_its_own_is_refused() -> None:
    with pytest.raises(export.SchemaNameError, match="descriptor's JSON value"):
        export._closed({DESCRIPTOR_JSON_MARK: True, "$ref": "#/$defs/X"})
    with pytest.raises(export.SchemaNameError, match="output's JSON value"):
        export._output_json({OUTPUT_JSON_MARK: True, "$ref": "#/$defs/X"})
    assert export._output_json({OUTPUT_JSON_MARK: True, "description": "d"}) == {
        "$ref": "#/$defs/OutputJson",
        "description": "d",
    }


def test_the_triggers_are_structural() -> None:
    """A definition is added where a ``$ref`` points at it, never where its text appears (a
    string value equal to the reference, which a search of the JSON text took for one)."""
    text = "#/$defs/OutputJson"
    assert not export._refers({"const": text, "description": text}, "OutputJson")
    assert not export._refers({"enum": [text], "x": {text: 1}}, "OutputJson")
    assert export._refers({"items": [{"$ref": "#/$defs/OutputJson"}]}, "OutputJson")
    assert not export._holds({"description": "x-aibi-document"}, "x-aibi-document")
    model = create_model(
        "Texty", __module__=MODULES[0], x=(str, Field(description=text, default=text))
    )
    assert "$defs" not in export.output_schema(model, "x")


def test_two_tools_of_one_name_are_refused_at_import() -> None:
    """``catalog.tools``' module run again with its first tool given twice: ``BY_NAME`` would keep
    one of them."""
    from aibi.core.catalog import tools

    tree = ast.parse(Path(tools.__file__).read_text(encoding="utf-8"))
    [assigned] = [
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "TOOLS"
    ]
    assert isinstance(assigned.value, ast.Tuple)
    assigned.value.elts.append(assigned.value.elts[0])
    module = types.ModuleType("aibi_naming_probe_tools")
    with pytest.raises(ValueError, match="two tools share a name"):
        exec(compile(tree, tools.__file__, "exec"), module.__dict__)


def test_a_request_and_a_document_defining_one_name_differently_are_refused() -> None:
    """``_documents``' merge: a request's own class named like a document's definition
    (``Cohort``) beside a document member."""
    cohort = create_model("Cohort", __module__=MODULES[0], mine=(int, ...))
    probe = create_model(
        "Probe", __module__=MODULES[0], document=(RequestDocument, ...), cohort=(cohort, ...)
    )
    with pytest.raises(export.SchemaNameError, match="Cohort is defined twice, differently"):
        export.request_schema(probe, "Probe")


# --- The builder-level differential property test -------------------------------------------------

WRITTEN = checked_in("document.as-written.schema.json")
JSON_DEFINITIONS = {
    "OutputJson": checked_in("result.schema.json")["$defs"]["OutputJson"],
    "RequestJson": checked_in("tool.validate-document.request.schema.json")["$defs"]["RequestJson"],
    "DescriptorJson": checked_in("proposal.schema.json")["$defs"]["DescriptorJson"],
    "DocumentJson": checked_in("document.schema.json")["$defs"]["DocumentJson"],
}
POOL = [
    "A",
    "B",
    "C",
    *sorted(export.RESERVED),
    "SubstitutedDocument",
    "SubstitutedA",
    "Substituted",
    *sorted(name for name in WRITTEN["$defs"] if name not in export.RESERVED)[:8],
]
KINDS = ["model", "enum", "root", "dataclass", "typeddict", "generic", "union"]
TRIALS = 1500
T = TypeVar("T")
GENERIC: Any = Generic.__class_getitem__(T)  # type: ignore[attr-defined]
MAKE: Any = create_model


@dataclasses.dataclass
class Spec:
    """A synthesised class and what the oracle expects of it: its kind, its unique mark, and
    each field's expectation (a ``Spec``, a leaf's label, or a twin's default)."""

    cls: Any
    kind: str
    mark: str
    fields: dict[str, Any]
    name: str


SAFE_TWINS: dict[str, Any] = {"Twin": 1, "Opt": True, "A": 1.0, "B": 0, "C": False}
"""The one value each twin's name takes in a tree that must be accepted: ``Twin`` as ``1`` in
a module and as ``true`` in another would be two classes of different schemas, refused."""


class Maker:
    """``safe`` makes the trials that must be accepted: unique class names, twins that are one,
    no name of the pool."""

    def __init__(self, rng: random.Random, safe: bool = False) -> None:
        self.rng = rng
        self.safe = safe
        self.count = 0

    def uid(self) -> str:
        self.count += 1
        return f"uid-{self.count}"

    def leaf(self, side: str) -> tuple[str, Any]:
        roll = self.rng.random()
        if side == "params":
            return ("json:DocumentJson", DocumentJson) if roll < 0.5 else ("int", int)
        if roll < 0.5:
            if side == "response":
                return "json:OutputJson", FiniteJsonObject
            return self.rng.choice(
                [("json:RequestJson", FiniteJsonObject), ("json:DescriptorJson", AnyJson)]
            )
        if roll < 0.65 and side == "request":
            return "document", RequestDocument
        return "int", int

    def make(self, depth: int, side: str, kind: str | None = None) -> Spec:
        rng = self.rng
        mark = self.uid()
        name = f"Safe{mark.removeprefix('uid-')}" if self.safe else rng.choice(POOL)
        module = rng.choice(MODULES)
        kind = kind or rng.choice(
            KINDS if depth > 0 else ["model", "model", "dataclass", "typeddict"]
        )
        if kind == "enum":
            members = {"a": f"{mark}-a", "b": f"{mark}-b"}
            return Spec(enum.Enum(name, members, module=module), "enum", mark, {}, name)
        fields: dict[str, Any] = {}
        types_: dict[str, Any] = {"mark_": Literal[mark]}
        if rng.random() < 0.35 and depth < 2:
            # Twins: one name in two modules, bodies apart by a bool, an int or a float (or alike).
            twin = rng.choice(["Twin", "Opt", *POOL[:3]])
            values = [1, True, 1.0, 0, False, 0.0]
            one = SAFE_TWINS.get(twin, 1) if self.safe else rng.choice(values)
            two = (
                one
                if self.safe or rng.random() < 0.25
                else rng.choice([v for v in values if v is not one])
            )
            for tag, value, where in (("t0", one, MODULES[0]), ("t1", two, MODULES[1])):
                types_[tag] = create_model(twin, __module__=where, x=(int | bool | float, value))
                fields[tag] = ("twin", value)
        if rng.random() < (0.5 if self.safe else 0.2) and depth < 2:
            # Nested twins: Outer(i: Inner) in each module, Inner.x alike or apart.
            one = 1 if self.safe else rng.choice([1, True, 1.0])
            two = one if self.safe or rng.random() < 0.5 else rng.choice([2, False, 0.5])
            for tag, value, where in (("n0", one, MODULES[0]), ("n1", two, MODULES[1])):
                inner = create_model("Inner", __module__=where, x=(int | bool | float, value))
                types_[tag] = create_model("Outer", __module__=where, i=(inner, ...))
                fields[tag] = ("nested", value)
        for index in range(rng.randint(1, 3)):
            field = f"f{index}"
            if rng.random() < 0.5 and depth < 2:
                child = self.make(depth + 1, side)
                fields[field] = child
                types_[field] = child.cls
            else:
                label, given = self.leaf(side)
                fields[field] = label
                types_[field] = given
        if kind == "root":
            inner = self.make(depth + 1, side, kind="model") if depth < 2 else None
            target: Any = inner.cls if inner else int
            cls = type(name, (RootModel[target],), {"__doc__": mark, "__module__": module})
            return Spec(cls, "root", mark, {"root": inner if inner else "int"}, name)
        if kind == "dataclass":
            cls = dataclasses.make_dataclass(name, list(types_.items()))
            cls.__doc__, cls.__module__ = mark, module
            return Spec(cls, "model", mark, fields, name)
        if kind == "typeddict":
            cls = TypedDict(name, types_)  # type: ignore[misc]
            cls.__doc__, cls.__module__ = mark, module
            return Spec(cls, "model", mark, fields, name)
        if kind == "generic":
            body = {"__annotations__": {"g": T, **types_}, "__doc__": mark, "__module__": module}
            base: Any = types.new_class(
                name, (BaseModel, GENERIC), exec_body=lambda ns: ns.update(body)
            )
            return Spec(base[int], "model", mark, {"g": "int", **fields}, name)
        if kind == "union":
            one, two = (self.make(min(depth + 1, 2), side, kind="model") for _ in range(2))
            members = []
            for tag, spec in (("a", one), ("b", two)):
                given = {
                    key: (value.cls if isinstance(value, Spec) else LEAVES.get(value), ...)
                    for key, value in spec.fields.items()
                    if not isinstance(value, tuple)
                }
                cls = MAKE(
                    spec.name,
                    __module__=module,
                    __doc__=spec.mark,
                    tag=(Literal[tag], ...),
                    mark_=(Literal[spec.mark], ...),
                    **given,
                )
                kept = {k: v for k, v in spec.fields.items() if not isinstance(v, tuple)}
                members.append(Spec(cls, "model", spec.mark, kept, spec.name))
            union = Annotated[Union[members[0].cls, members[1].cls], Field(discriminator="tag")]  # noqa: UP007
            return Spec(union, "union", mark, {"a": members[0], "b": members[1]}, name)
        cls = MAKE(
            name, __doc__=mark, __module__=module, **{k: (v, ...) for k, v in types_.items()}
        )
        return Spec(cls, "model", mark, fields, name)


LEAVES: dict[str, Any] = {
    "json:OutputJson": FiniteJsonObject,
    "json:RequestJson": FiniteJsonObject,
    "json:DescriptorJson": AnyJson,
    "json:DocumentJson": DocumentJson,
    "document": RequestDocument,
    "int": int,
}


def resolve(node: Any, defs: dict[str, Any]) -> Any:
    for _ in range(30):
        if not (isinstance(node, dict) and "$ref" in node and set(node) <= {"$ref", "title"}):
            return node
        reference = node["$ref"]
        if not reference.startswith("#/$defs/"):
            return node
        node = defs.get(reference.removeprefix("#/$defs/"), {"MISSING": reference})
    return node


def refs(node: Any) -> Iterator[str]:
    if isinstance(node, list):
        for item in node:
            yield from refs(item)
    elif isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            yield node["$ref"].removeprefix("#/$defs/")
        for value in node.values():
            yield from refs(value)


def check_leaf(
    label: str, member: Any, defs: dict[str, Any], side: str, at: str, wrong: list[str]
) -> None:
    if label == "int":
        if member.get("type") != "integer":
            wrong.append(f"{at}: int is {json.dumps(member)[:80]}")
        return
    if label == "document":
        if member.get("description") != export.WRITTEN_TEXT:
            wrong.append(f"{at}: document root is {json.dumps(member)[:80]}")
        pending, seen = list(refs(member)), set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            if defs.get(name) != WRITTEN["$defs"].get(name):
                wrong.append(f"{at}: document definition {name} differs")
                return
            pending.extend(refs(defs[name]))
        return
    kind = label.split(":")[1]
    expected = JSON_DEFINITIONS[
        "RequestJson" if side == "request" and kind == "OutputJson" else kind
    ]
    names = list(refs(member))
    if len(names) != 1:
        wrong.append(f"{at}: {label} has {len(names)} references: {json.dumps(member)[:80]}")
    elif defs.get(names[0]) != expected:
        wrong.append(f"{at}: {label} refers to {names[0]}: {json.dumps(defs.get(names[0]))[:80]}")


def mark_of(node: Any) -> Any:
    if not isinstance(node, dict):
        return None
    member = node.get("properties", {}).get("mark_", {})
    return member.get("const") if isinstance(member, dict) else None


def check(
    spec: Spec,
    node: Any,
    defs: dict[str, Any],
    side: str,
    at: str,
    wrong: list[str],
    root: bool = False,
) -> None:
    node = node if root else resolve(node, defs)
    if spec.kind == "enum":
        if not (isinstance(node, dict) and node.get("enum") == [m.value for m in spec.cls]):
            wrong.append(f"{at}: enum {spec.name} -> {json.dumps(node)[:100]}")
        return
    if spec.kind == "union":
        members = node.get("oneOf") or node.get("anyOf") or []
        marks = {mark_of(resolve(m, defs)) for m in members}
        if len(members) != 2 or marks != {spec.fields["a"].mark, spec.fields["b"].mark}:
            wrong.append(f"{at}: union -> {json.dumps(node)[:100]}")
            return
        for member in members:
            found = resolve(member, defs)
            sub = spec.fields["a"] if mark_of(found) == spec.fields["a"].mark else spec.fields["b"]
            check(sub, found, defs, side, at + "|", wrong, root=True)
        return
    if spec.kind == "root":
        if not (isinstance(node, dict) and node.get("description") == spec.mark):
            wrong.append(f"{at}: root {spec.name}({spec.mark}) -> {json.dumps(node)[:120]}")
            return
        child = spec.fields["root"]
        body = {k: v for k, v in node.items() if k not in ("description", "title")}
        if isinstance(child, Spec):
            check(child, body, defs, side, at + ".root", wrong)
        elif body.get("type") != "integer":
            wrong.append(f"{at}.root: int is {json.dumps(body)[:80]}")
        return
    if not (isinstance(node, dict) and mark_of(node) == spec.mark):
        wrong.append(f"{at}: {spec.kind} {spec.name}({spec.mark}) -> {json.dumps(node)[:120]}")
        return
    properties = node.get("properties", {})
    for field, child in spec.fields.items():
        member = properties.get(field)
        if member is None:
            wrong.append(f"{at}.{field}: missing")
        elif isinstance(child, Spec):
            check(child, member, defs, side, f"{at}.{field}", wrong)
        elif isinstance(child, tuple):
            body = resolve(member, defs)
            if child[0] == "nested":
                body = resolve(body.get("properties", {}).get("i", {}), defs)
            got = body.get("properties", {}).get("x", {}).get("default", "MISSING")
            if json.dumps(got) != json.dumps(child[1]):
                wrong.append(
                    f"{at}.{field}: {child[0]} default {json.dumps(got)} for {json.dumps(child[1])}"
                )
        else:
            check_leaf(child, member, defs, side, f"{at}.{field}", wrong)


def trial(maker: Maker) -> tuple[str, str]:
    """One random class tree through one public builder: ``request_schema``, ``output_schema``,
    ``values_schema`` (a response's model) or ``params_schema`` (a model with document JSON)."""
    rng = maker.rng
    side = rng.choice(["request", "response", "response", "params"])
    while True:
        spec = maker.make(0, side)
        if side != "params" or (isinstance(spec.cls, type) and issubclass(spec.cls, BaseModel)):
            break
    is_model = isinstance(spec.cls, type) and issubclass(spec.cls, BaseModel)
    build: Callable[[Any], Any]
    if side == "params":
        build = export.params_schema
    elif side == "request":
        build = lambda model: export.request_schema(model, "Probe")  # noqa: E731
    elif is_model and rng.random() < 0.4:
        build = export.values_schema
    else:
        build = lambda model: export.output_schema(model, "Probe")  # noqa: E731
    try:
        schema: Any = build(spec.cls)
    except ValueError as error:
        return ("OVERREFUSED" if maker.safe else "refused"), str(error)[:60]
    defs: Any = schema.get("$defs", {})
    root = resolve(schema, defs) if "$ref" in schema and not schema.get("properties") else schema
    wrong: list[str] = []
    check(spec, root, defs, side, side, wrong, root=True)
    if MARK in json.dumps(schema):
        wrong.append("a public output carries the marker")
    return ("WRONG", " | ".join(wrong)[:300]) if wrong else ("ok", "")


def run(trials: int, seed: int = 2020, safe: bool = False) -> Counter[str]:
    maker = Maker(random.Random(seed), safe=safe)
    tally: Counter[str] = Counter()
    examples: list[str] = []
    for _ in range(trials):
        outcome, why = trial(maker)
        tally[outcome] += 1
        if outcome in ("WRONG", "OVERREFUSED") and len(examples) < 3:
            examples.append(why)
    tally.update({f"example: {e}": 0 for e in examples})
    return tally


def define_bare(defs: dict[str, Any], name: str) -> None:
    """``_define_reserved`` as a bare assignment (the injection sites alone, ``_documents``'
    ``_define`` kept)."""
    defs[name] = (
        export.PARAMETER_REFERENCE if name == "ParameterReference" else export._json_value(name)
    )


ISOLATION: dict[str, dict[str, Any]] = {
    "both layers": {},
    "the pre-check off (export._refuse_reserved)": {"_refuse_reserved": lambda originals: None},
    "the injection sites' _define off (export._define_reserved)": {"_define_reserved": define_bare},
}


@pytest.mark.parametrize("mode", list(ISOLATION))
def test_every_field_is_described_by_its_own_class_or_the_builder_refuses(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """The differential property, 1,500 seeded trials per mode, each layer also alone: no
    accepted schema describes a field by another class (or a JSON value, a document, a twin's
    default) than its own. A refusal counts as success here, so this test cannot see a builder
    that refuses too much: ``test_a_tree_of_classes_that_names_cannot_confuse_is_accepted`` does."""
    for site, patched in ISOLATION[mode].items():
        monkeypatch.setattr(export, site, patched)
    tally = run(TRIALS)
    examples = [key for key in tally if key.startswith("example")]
    assert tally["WRONG"] == 0, examples
    assert tally["ok"] > 300, dict(tally)
    assert tally["refused"] > 300, dict(tally)


SAFE_TRIALS = 300


@pytest.mark.parametrize("mode", list(ISOLATION))
def test_a_tree_of_classes_that_names_cannot_confuse_is_accepted(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """The expected-accepted subset: unique class names, twins in two modules with equal schemas
    (a leaf twin, and nested twins ``Outer(i: Inner)``, which Pydantic merges and ``Strict``
    must too), the builders' own JSON values: no trial may be refused, and each is described by
    its own classes as the oracle says. Over-refusal fails here."""
    for site, patched in ISOLATION[mode].items():
        monkeypatch.setattr(export, site, patched)
    tally = run(SAFE_TRIALS, seed=4040, safe=True)
    examples = [key for key in tally if key.startswith("example")]
    assert tally["WRONG"] == 0, examples
    assert tally["OVERREFUSED"] == 0, examples
    assert tally["ok"] == SAFE_TRIALS, dict(tally)

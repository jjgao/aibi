"""Checks on every descriptor write and the versions the server sets (SPEC §5.1, §10.1, D243,
D247), with a test-only pack of shelf marks."""

import time
import tracemalloc
from typing import Any

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter

import aibi
from aibi.core.engine import build
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.jsonschemas import (
    OUT_OF_STEPS,
    STEPS_BASE,
    STEPS_MAX,
    STEPS_PER_VALUE,
    UNEVALUABLE,
    WRITE_STEPS_MAX,
    Checker,
    Failure,
    problems,
    steps,
)
from aibi.core.schema.limits import MAX_LIST, MAX_VALUES
from aibi.core.schema.pack_api import Pack, PackError, PackManifest, PackRegistry
from aibi.core.store.tombstones import Tombstone
from aibi.core.store.writes import check_writes, versions

_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)
MANIFEST = PackManifest(id="shelf", version="1.0.0", results_version=1, requires_core=">=0.0.1")
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"mark": {"type": "string", "enum": ["A", "B"]}},
    "additionalProperties": False,
}


def registry(**given: Any) -> PackRegistry:
    fields: dict[str, Any] = {
        "manifest": MANIFEST,
        "extension_schemas": {"dataset": SCHEMA},
        "ontology_systems": {"marks": lambda code: code.startswith("M")},
    }
    fields.update(given)
    return PackRegistry([Pack(**fields)], core_version=aibi.__version__)


def descriptors(mark: Any = "A", *, packs: tuple[str, ...] = ("shelf",)) -> list[Descriptor]:
    dataset = build.dataset(packs=list(packs))
    dumped = dataset.model_dump(mode="json")
    dumped["extensions"] = {"shelf": {"mark": mark}}
    dumped["curation"]["/extensions/shelf/mark"] = dumped["curation"]["/label"]
    return [_ADAPTER.validate_python(dumped)]


def codes(found: list[Any]) -> list[tuple[str, str | None]]:
    return [(str(refusal.code), refusal.path) for refusal in found]


def test_an_extension_that_matches_its_pack_s_schema_passes() -> None:
    assert check_writes(descriptors(), registry()) == []


def test_an_extension_that_breaks_its_pack_s_schema_is_refused_at_the_member() -> None:
    [refusal] = check_writes(descriptors("C"), registry())
    assert (refusal.code, refusal.path) == ("INVALID_EXTENSION", "/0/extensions/shelf/mark")
    assert "C" not in refusal.model_dump_json()  # the value is never repeated (A6)


def test_a_listed_pack_that_is_not_registered_is_refused() -> None:
    assert codes(check_writes(descriptors(), None)) == [("INVALID_VALUE", "/0/fields/packs/0")]


def test_a_pack_without_a_schema_for_the_kind_refuses_its_extensions() -> None:
    found = check_writes(descriptors(), registry(extension_schemas={}))
    assert codes(found) == [("INVALID_EXTENSION", "/0/extensions/shelf")]


def test_an_ontology_validator_rejects_a_code_and_an_unknown_system_passes() -> None:
    column = build.column(
        "t.a",
        "string",
        concepts=[
            {"system": "marks", "code": "M1", "label": "One", "relation": "exact"},
            {"system": "marks", "code": "X1", "label": "Ex", "relation": "exact"},
            {"system": "other", "code": "X1", "label": "Ex", "relation": "exact"},
        ],
    )
    found = check_writes([*descriptors(), column], registry())
    assert codes(found) == [("INVALID_VALUE", "/1/fields/concepts/1/code")]


@pytest.mark.parametrize(
    ("schema", "problem"),
    [
        ({"type": "object", "properties": {"mark": {"type": "strin"}}}, "not a valid schema"),
        ({"$ref": "https://example.org/schema.json"}, "not local"),
        ({"$schema": "http://json-schema.org/draft-07/schema#"}, "dialect"),
        ({"$dynamicRef": "https://example.org/schema.json"}, "not local"),
        (
            {"properties": {"mark": {"$schema": "http://json-schema.org/draft-04/schema#"}}},
            "a subschema declares a dialect",
        ),
        ({"$ref": "#"}, "refers to itself"),
        (
            {
                "$defs": {
                    "a": {"allOf": [{"$ref": "#/$defs/b"}]},
                    "b": {"not": {"$ref": "#/$defs/a"}},
                },
                "$ref": "#/$defs/a",
            },
            "refers to itself",
        ),
        ({"properties": {"mark": {"$ref": "#/$defs/nope"}}}, "resolves to nothing"),
        ({"allOf": [{}], "properties": {"mark": {"$ref": "#/allOf/x"}}}, "resolves to nothing"),
        ({"type": "object", "properties": {"mark": {"$ref": "#/type/x"}}}, "resolves to nothing"),
        ({"$dynamicRef": "#meta"}, "resolves to nothing"),
        (
            {
                "$defs": {
                    "x": {"$id": "https://example.org/x", "properties": {"a": {"$ref": "#/y"}}}
                }
            },
            "resolves to nothing",
        ),
        ({"properties": {"mark": {"type": "string", "pattern": "^(a+)+$"}}}, "pattern"),
        ({"patternProperties": {"^m": {"type": "string"}}}, "patternProperties"),
        # What a reference resolves to is checked wherever it is, under unknown keywords too.
        ({"properties": {"a": {"$ref": "#/x"}}, "x": {"pattern": "^(a+)+$"}}, "pattern"),
        ({"properties": {"a": {"$ref": "#/x/y"}}, "x": {"y": {"pattern": "^(a+)+$"}}}, "pattern"),
        (
            {"properties": {"a": {"$ref": "#/x"}}, "x": {"$ref": "#/y"}, "y": {"pattern": "a"}},
            "pattern",
        ),
        (
            {
                "properties": {"a": {"$ref": "#/x"}},
                "x": {"$schema": "http://json-schema.org/draft-04/schema#", "type": "string"},
            },
            "a subschema declares a dialect",
        ),
        ({"properties": {"a": {"$ref": "#/x"}}, "x": {"$ref": "#/nope"}}, "resolves to nothing"),
        ({"properties": {"a": {"$ref": "#/x"}}, "x": [1]}, "not a schema"),
        ({"properties": {"a": {"$ref": "#/x"}}, "x": {"type": "strin"}}, "not a valid schema"),
        ({"$ref": "#/x", "x": {"$ref": "#"}}, "refers to itself"),
        # Cycles through every in-place applicator.
        ({"anyOf": [{"$ref": "#"}]}, "refers to itself"),
        ({"oneOf": [{"$ref": "#"}]}, "refers to itself"),
        ({"if": {"$ref": "#"}}, "refers to itself"),
        ({"then": {"$ref": "#"}}, "refers to itself"),
        ({"else": {"$ref": "#"}}, "refers to itself"),
        ({"dependentSchemas": {"mark": {"$ref": "#"}}}, "refers to itself"),
    ],
)
def test_registration_refuses_an_extension_schema_that_is_not_local_json_schema_2020_12(
    schema: dict[str, Any], problem: str
) -> None:
    with pytest.raises(PackError, match=problem):
        registry(extension_schemas={"dataset": schema})


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"mark": {"$ref": "#/$defs/mark"}}, "$defs": {"mark": {}}},
        {"properties": {"mark": {"$ref": "#"}}},
        {"properties": {"pattern": {"type": "string"}}},
        {
            "$defs": {"m": {"$dynamicAnchor": "meta"}},
            "properties": {"mark": {"$dynamicRef": "#meta"}},
        },
    ],
)
def test_registration_accepts_references_that_resolve_and_descend(schema: dict[str, Any]) -> None:
    assert check_writes(descriptors(), registry(extension_schemas={"dataset": schema})) == []


def test_a_value_its_schema_cannot_evaluate_is_refused_not_raised() -> None:
    assert Checker({"$ref": "#/$defs/nope"}).failures({"mark": "A"}) == [Failure((), UNEVALUABLE)]
    deep: Any = "A"
    for _ in range(5000):
        deep = {"mark": deep}
    recursive = {"type": "object", "properties": {"mark": {"$ref": "#"}}}
    assert Checker(recursive).failures(deep) == [Failure((), UNEVALUABLE)]


def _nested(depth: int) -> Any:
    value: Any = {}
    for _ in range(depth):
        value = {"next": value}
    return value


RECURSIVE_ANY_OF: dict[str, Any] = {
    "$defs": {
        "node": {
            "anyOf": [
                {"properties": {"next": {"$ref": "#/$defs/node"}}, "required": ["a"]},
                {"properties": {"next": {"$ref": "#/$defs/node"}}, "required": ["b"]},
            ]
        }
    },
    "$ref": "#/$defs/node",
}


def test_a_schema_recursing_under_any_of_is_cut_off_in_time_linear_in_the_value() -> None:
    """D247: every level evaluates both branches, each descending again, so jsonschema's work
    doubles with each level (25 s and 1.6 GiB at depth 16); the step budget stops it."""
    checker = Checker(RECURSIVE_ANY_OF)
    assert checker.failures(_nested(2)) == [Failure((), "anyOf")]
    started = time.monotonic()
    for depth in (16, 32, 63):
        assert checker.failures(_nested(depth)) == [Failure((), OUT_OF_STEPS)]
    assert time.monotonic() - started < 2


def test_a_schema_recursing_under_any_of_is_refused_at_the_extension_object() -> None:
    schema = {
        "$defs": RECURSIVE_ANY_OF["$defs"],
        "type": "object",
        "properties": {"mark": {"$ref": "#/$defs/node"}},
    }
    found = check_writes(descriptors(_nested(20)), registry(extension_schemas={"dataset": schema}))
    assert codes(found) == [("LIMIT_EXCEEDED", "/0/extensions/shelf")]
    [refusal] = found
    assert refusal.limit is not None
    assert refusal.limit.model_dump() == {
        "name": "extension_steps",
        "max": steps({"mark": _nested(20)}),
    }


def test_the_step_budget_leaves_a_large_value_of_a_plain_schema_alone() -> None:
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"k": {"type": "integer"}, "j": {"type": "string", "maxLength": 8}},
            "required": ["k"],
        },
        "uniqueItems": True,
    }
    many: list[Any] = [{"k": i, "j": "x"} for i in range(10_000)]
    assert Checker(schema).failures(many) == []
    assert Checker(schema).failures([*many, {"j": "y"}]) == [Failure((10_000,), "required")]


def _padded(depth: int, pad: int) -> Any:
    value = _nested(depth)
    value["pad"] = [0] * pad
    return value


def test_the_step_budget_does_not_grow_with_padding_or_the_schema_s_keywords() -> None:
    """D247: the budget was 2 steps per keyword of the schema per value, so 20 unused string
    properties and 30,000 zeros where the schema does not look (60 KB, under the proposal cap)
    bought the recursion 74 s and 3.4 GB; it is now at most ``STEPS_MAX`` whatever the schema."""
    schema = {
        **RECURSIVE_ANY_OF,
        "properties": {f"s{i}": {"type": "string", "maxLength": 3} for i in range(20)},
        "examples": [{"type": "string", "enum": [], "const": 1}] * 50,
    }
    started = time.monotonic()
    assert Checker(schema).failures(_padded(40, 30_000)) == [Failure((), OUT_OF_STEPS)]
    assert Checker(RECURSIVE_ANY_OF).failures(_padded(40, 30_000)) == [Failure((), OUT_OF_STEPS)]
    assert time.monotonic() - started < 6


def _branching(keyword: str, levels: int, leaf: dict[str, Any]) -> dict[str, Any]:
    """A schema whose every level applies the next twice, in place: 2^levels leaves."""
    defs: dict[str, Any] = {
        f"n{i}": {keyword: [{"$ref": f"#/$defs/n{i + 1}"}, {"$ref": f"#/$defs/n{i + 1}"}]}
        for i in range(levels)
    }
    defs[f"n{levels}"] = leaf
    return {"$defs": defs, "$ref": "#/$defs/n0"}


def test_errors_keep_no_copy_of_the_value_so_memory_stays_small() -> None:
    """D247: jsonschema's messages repeat the value, and its ``anyOf`` keeps every error of its
    subschemas, so each failing leaf here held the 60 KB object again."""
    checker = Checker(_branching("anyOf", 24, {"type": "string"}))
    padded: Any = {"pad": [0] * 20_000}
    tracemalloc.start()
    try:
        assert checker.failures(padded, ceiling=30_000) == [Failure((), OUT_OF_STEPS)]
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 16 * 2**20


def test_recursion_that_finds_no_error_spends_steps_too() -> None:
    """Every keyword evaluation spends a step, not only failing ones: a valid value under
    ``allOf`` twice over the same reference is evaluated 2^depth times."""
    twice = {
        "$defs": {
            "node": {
                "allOf": [
                    {"properties": {"next": {"$ref": "#/$defs/node"}}},
                    {"properties": {"next": {"$ref": "#/$defs/node"}}},
                ]
            }
        },
        "$ref": "#/$defs/node",
    }
    checker = Checker(twice)
    assert checker.failures(_nested(3)) == []
    started = time.monotonic()
    assert checker.failures(_nested(16)) == [Failure((), OUT_OF_STEPS)]
    assert time.monotonic() - started < 1


def test_every_error_spends_a_step() -> None:
    required: Any = {"required": [f"m{i}" for i in range(20_000)]}
    assert Checker(required).failures({}) == [Failure((), OUT_OF_STEPS)]
    assert Checker({"required": ["a", "b"]}).failures({}) == [
        Failure((), "required"),
        Failure((), "required"),
    ]


def test_keywords_that_go_through_every_item_spend_a_step_per_item() -> None:
    """``items`` over ``true`` spends no step in its subschema; repeated in place 2^10 times
    over 10,000 items, or 300 arrays of 100, it would go through tens of millions of values."""
    started = time.monotonic()
    for leaf in ({"items": True}, {"contains": True}, {"uniqueItems": True}):
        checker = Checker(_branching("allOf", 10, leaf))
        assert checker.failures(list(range(10_000))) == [Failure((), OUT_OF_STEPS)]
    for leaf in ({"additionalProperties": True}, {"propertyNames": True}):
        checker = Checker(_branching("allOf", 10, leaf))
        assert checker.failures({f"k{i}": i for i in range(10_000)}) == [Failure((), OUT_OF_STEPS)]
    nested: Any = [list(range(start, start + 100)) for start in range(300)]
    checker = Checker(_branching("allOf", 12, {"uniqueItems": True}))
    assert checker.failures(nested) == [Failure((), OUT_OF_STEPS)]
    assert time.monotonic() - started < 2


def test_messages_never_repeat_the_value_so_errors_cost_little() -> None:
    """jsonschema's messages repeat the value they are about: here each of thousands of failing
    leaves would write out a megabyte again, while the budget counts the string as one value,
    or 20,000 values again (30 s to 70 s in all, where it takes about 1 s)."""
    checker = Checker(_branching("anyOf", 24, {"type": "integer"}))
    long = "x" * 1_000_000
    started = time.monotonic()
    assert checker.failures(long) == [Failure((), OUT_OF_STEPS)]
    assert checker.failures({"pad": long}) == [Failure((), OUT_OF_STEPS)]
    assert checker.failures([long]) == [Failure((), OUT_OF_STEPS)]
    assert time.monotonic() - started < 1
    containers: list[Any] = [
        {"pad": [0] * 20_000},
        [0] * 20_000,
        {f"k{i}": 0 for i in range(20_000)},
    ]
    for value in containers:
        started = time.monotonic()
        assert checker.failures(value) == [Failure((), OUT_OF_STEPS)]
        assert time.monotonic() - started < 5


def test_unevaluated_items_and_properties_take_linear_time() -> None:
    """D247: jsonschema tests membership against a list, so 32,000 items took 5.4 s."""
    many: Any = list(range(32_000))
    members: dict[str, Any] = {f"k{i}": i for i in range(20_000)}
    started = time.monotonic()
    assert Checker({"items": True, "unevaluatedItems": False}).failures(many) == []
    assert Checker({"unevaluatedItems": {"type": "integer"}}).failures(many) == []
    assert Checker({"prefixItems": [{}], "unevaluatedItems": False}).failures(many) == [
        Failure((), "unevaluatedItems")
    ]
    closed = {"additionalProperties": {"type": "integer"}, "unevaluatedProperties": False}
    assert Checker(closed).failures(members) == []
    assert Checker({"properties": {"k0": {}}, "unevaluatedProperties": False}).failures(
        members
    ) == [Failure((), "unevaluatedProperties")]
    assert time.monotonic() - started < 2


_SMALL = st.recursive(
    st.none() | st.booleans() | st.integers(-1, 2) | st.sampled_from(["", "a"]),
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.sampled_from(["a", "b", "c"]), inner, max_size=3)
    ),
    max_leaves=8,
)
_LEAVES = st.sampled_from(
    [
        True,
        False,
        {},
        {"type": "integer"},
        {"type": "string"},
        {"type": "array"},
        {"type": "object"},
        {"minItems": 2},
        {"required": ["a"]},
        {"const": 1},
        {"$ref": "#/$defs/d"},
        {"$dynamicRef": "#/$defs/d"},
    ]
)


def _applying(inner: st.SearchStrategy[Any]) -> st.SearchStrategy[Any]:
    names = st.sampled_from(["a", "b"])
    return st.one_of(
        st.builds(lambda s: {"items": s}, inner),
        st.builds(lambda a: {"prefixItems": a}, st.lists(inner, max_size=2)),
        st.builds(lambda s: {"contains": s}, inner),
        st.builds(lambda s: {"unevaluatedItems": s}, inner),
        st.builds(lambda s: {"unevaluatedProperties": s}, inner),
        st.builds(lambda d: {"properties": d}, st.dictionaries(names, inner, max_size=2)),
        st.builds(lambda s: {"additionalProperties": s}, inner),
        st.builds(lambda d: {"dependentSchemas": d}, st.dictionaries(names, inner, max_size=1)),
        st.builds(
            lambda k, a: {k: a},
            st.sampled_from(["allOf", "anyOf", "oneOf"]),
            st.lists(inner, min_size=1, max_size=3),
        ),
        st.builds(lambda i, t, e: {"if": i, "then": t, "else": e}, inner, inner, inner),
        st.builds(lambda s: {"not": s}, inner),
        st.builds(
            lambda a, b: {**a, **b} if isinstance(a, dict) and isinstance(b, dict) else a,
            inner,
            inner,
        ),
    )


_SCHEMAS = st.recursive(_LEAVES, _applying, max_leaves=10)


def _found(errors: Any) -> list[tuple[tuple[Any, ...], str]]:
    return sorted(
        ((tuple(e.absolute_path), str(e.validator)) for e in errors),
        key=lambda found: ([str(t) for t in found[0]], found[1]),
    )


@pytest.mark.parametrize(
    ("schema", "values"),
    [
        ({"prefixItems": [{}], "unevaluatedItems": False}, [[1], [1, 2]]),
        (
            {
                "allOf": [{"unevaluatedProperties": {"type": "integer"}}],
                "unevaluatedProperties": False,
            },
            [{"a": 1}, {"a": "x"}],
        ),
        (
            {"allOf": [{"unevaluatedItems": {"type": "integer"}}], "unevaluatedItems": False},
            [[1], ["x"]],
        ),
        (
            {
                "dependentSchemas": {"a": {"properties": {"b": {}}}},
                "properties": {"a": {}},
                "unevaluatedProperties": False,
            },
            [{"a": 1, "b": 2}, {"b": 2}],
        ),
        (
            {
                "if": {"prefixItems": [{"type": "integer"}]},
                "then": {"prefixItems": [{}, {}]},
                "else": {"items": {}},
                "unevaluatedItems": False,
            },
            [[1, 2], [1, 2, 3], ["x", 2]],
        ),
        (
            {
                "if": {"properties": {"a": {"type": "integer"}}},
                "then": {"properties": {"b": {}}},
                "else": {"properties": {"c": {}}},
                "unevaluatedProperties": False,
            },
            [{"a": 1, "b": 1}, {"a": 1, "c": 1}, {"a": "x", "c": 1}, {"a": "x", "b": 1}],
        ),
        (
            {"contains": {"type": "string"}, "unevaluatedItems": {"type": "integer"}},
            [["a", 1], ["a", None]],
        ),
        (
            {
                "$defs": {"p": {"properties": {"a": {}}}, "i": {"prefixItems": [{}]}},
                "anyOf": [{"$ref": "#/$defs/p"}, {"$ref": "#/$defs/i"}],
                "unevaluatedProperties": False,
                "unevaluatedItems": False,
            },
            [{"a": 1}, {"a": 1, "b": 2}, [1], [1, 2]],
        ),
        (
            {
                "anyOf": [
                    {"properties": {"a": {"type": "integer"}}},
                    {"properties": {"b": {}}},
                ],
                "unevaluatedProperties": False,
            },
            [{"a": "x", "b": 1}, {"a": 1, "b": 1}, {"a": "x"}],
        ),
        (
            {
                "oneOf": [
                    {"prefixItems": [{"type": "integer"}]},
                    {"prefixItems": [{"type": "string"}]},
                ],
                "unevaluatedItems": False,
            },
            [[1], ["a"], [1, 2], [None]],
        ),
        (
            {"additionalProperties": {"type": "integer"}, "unevaluatedProperties": False},
            [{"a": 1}, {"a": "x"}],
        ),
        ({"oneOf": [{"type": "integer"}, {"minimum": 0}]}, [1, -1, 0.5, "x"]),
        (
            {"allOf": [{"items": True}, {"prefixItems": [True]}], "unevaluatedItems": False},
            [[1, 2, 3], [1]],
        ),
        (
            {
                "if": {"required": ["x"]},
                "else": {"properties": {"a": True}},
                "unevaluatedProperties": False,
            },
            [{"a": 1}, {"x": 1, "a": 1}, {"b": 1}],
        ),
        (
            {
                "$defs": {"p": {"$dynamicAnchor": "p", "properties": {"a": True}}},
                "$dynamicRef": "#p",
                "unevaluatedProperties": False,
            },
            [{"a": 1}, {"a": 1, "b": 2}],
        ),
        (
            {
                "$defs": {"i": {"$dynamicAnchor": "i", "prefixItems": [True]}},
                "$dynamicRef": "#i",
                "unevaluatedItems": False,
            },
            [[1], [1, 2]],
        ),
    ],
)
def test_the_overridden_keywords_agree_with_jsonschema_on_each_branch(
    schema: dict[str, Any], values: list[Any]
) -> None:
    assert problems(schema) == []
    for value in values:
        ours = [(failure.path, failure.keyword) for failure in Checker(schema).failures(value)]
        assert ours == _found(Draft202012Validator(schema).iter_errors(value))


@settings(max_examples=1500, deadline=None)
@given(_SCHEMAS, _SCHEMAS, _SMALL)
def test_the_checker_fails_what_jsonschema_fails_where_it_does(
    schema: Any, defined: Any, value: Any
) -> None:
    """D247: the overridden keywords (``anyOf``, ``oneOf``, ``unevaluatedItems``,
    ``unevaluatedProperties``) and the quiet copy of the value change no result."""
    full = {"$defs": {"d": defined}, "allOf": [schema]}
    assume(problems(full) == [])
    ours = [(failure.path, failure.keyword) for failure in Checker(full).failures(value)]
    assert ours == _found(Draft202012Validator(full).iter_errors(value))


def test_unique_items_takes_linear_time_on_objects() -> None:
    """D247: jsonschema compares every pair of objects; 10,000 of them took 80 s."""
    checker = Checker({"type": "array", "uniqueItems": True})
    many: list[Any] = [{"k": i} for i in range(10_000)]
    started = time.monotonic()
    assert checker.failures(many) == []
    assert checker.failures([*many, {"k": 5}]) == [Failure((), "uniqueItems")]
    mixed: list[Any] = [i if i % 2 else str(i) for i in range(10_000)]
    assert checker.failures(mixed) == []
    assert time.monotonic() - started < 1


def test_unique_items_false_asks_nothing() -> None:
    assert Checker({"uniqueItems": False}).failures([1, 1.0, {"a": 1}, {"a": 1}]) == []


def _equal(left: Any, right: Any) -> bool:
    """JSON Schema's equality, pair by pair: numbers by value, never booleans; arrays item by
    item; objects member by member."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, int | float) and isinstance(right, int | float):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(map(_equal, left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    return type(left) is type(right) and left == right


def _unique(items: list[Any]) -> bool:
    return not any(
        _equal(items[i], items[j]) for i in range(len(items)) for j in range(i + 1, len(items))
    )


def test_unique_items_finds_equal_items_that_jsonschema_s_sorting_misses() -> None:
    """D247: jsonschema sorts the items first, and Python orders ``[1]`` and ``[True]`` as equal,
    so it never compares the two ``[1]``."""
    items: list[Any] = [[1], [True], [1]]
    assert Draft202012Validator({"uniqueItems": True}).is_valid(items)
    assert Checker({"uniqueItems": True}).failures(items) == [Failure((), "uniqueItems")]


@pytest.mark.parametrize(
    "items",
    [
        [],
        [1, 1.0],
        [0, -0.0],
        [True, 1],
        [False, 0],
        [True, True],
        [None, False],
        [None, None],
        ["1", 1],
        ["a", "a"],
        [{"a": 1}, {"a": 1.0}],
        [{"a": 1, "b": 2}, {"b": 2, "a": 1}],
        [{"a": True}, {"a": 1}],
        [[1], [1.0]],
        [[True], [1]],
        [[1, 2], [2, 1]],
        [{"a": [1, {"b": None}]}, {"a": [1.0, {"b": None}]}],
        [{}, []],
        [1e16, 10_000_000_000_000_000],
        [0.5, 0.25, 0.5],
        [[1], [True], [1]],
        [{"a": [1]}, {"a": [True]}, {"a": [1.0]}],
    ],
)
def test_unique_items_is_json_schema_s_equality(items: list[Any]) -> None:
    expected = [] if _unique(items) else [Failure((), "uniqueItems")]
    assert Checker({"uniqueItems": True}).failures(items) == expected


_JSON = st.recursive(
    st.none() | st.booleans() | st.integers(-3, 3) | st.sampled_from([0.0, -0.0, 1.0, 2.5]),
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.sampled_from(["a", "b"]), inner, max_size=2)
    ),
    max_leaves=6,
)


@settings(max_examples=300, deadline=None)
@given(st.lists(_JSON, max_size=5))
def test_unique_items_is_json_schema_s_equality_on_any_json(items: list[Any]) -> None:
    unique = Checker({"uniqueItems": True}).failures(items) == []
    assert unique == _unique(items)


def test_an_extension_of_a_pack_neither_listed_nor_registered_is_refused() -> None:
    dumped = descriptors(packs=())[0].model_dump(mode="json")
    found = check_writes([_ADAPTER.validate_python(dumped)], None)
    assert codes(found) == [("INVALID_EXTENSION", "/0/extensions/shelf")]


def test_a_version_is_bumped_only_when_something_but_the_version_differs() -> None:
    base = [build.table("t", ["a"]), build.column("t.a", "string")]
    bumped = base[0].model_copy(update={"label": "Changed"})
    found = {d.id: d.version for d in versions(base, [bumped, base[1]])}
    assert found == {"t": 2, "t.a": 1}
    reverted = versions(base, [bumped.model_copy(update={"label": "t", "version": 2}), base[1]])
    assert [d.version for d in reverted] == [1, 1]


def test_a_new_descriptor_has_version_one_and_a_returning_one_its_tombstone_s_plus_one() -> None:
    new, back = build.table("n"), build.table("r")
    found = versions([], [new, back], [Tombstone("r", "", {}, 4)])
    assert [d.version for d in found] == [1, 5]


# --- The step limit, and what a scoped check evaluates ------------------------------------------


def _required(count: int) -> Checker:
    """A schema that spends a step on its one keyword and a step on each of ``count`` errors."""
    return Checker({"required": [f"m{i}" for i in range(count)]})


def test_the_step_budget_is_its_base_and_eight_steps_per_value_exactly() -> None:
    budget = STEPS_BASE + STEPS_PER_VALUE
    assert steps({}) == budget == 10_008
    assert len(_required(budget - 1).failures({})) == budget - 1
    assert _required(budget).failures({}) == [Failure((), OUT_OF_STEPS)]


def test_the_ceiling_caps_the_steps_a_large_value_spends() -> None:
    """D247: the budget of 20,002 values would be 170,016 steps; a proposal's evaluation spends
    at most ``STEPS_MAX`` of them, and an operator's or an importer's write all of them."""
    padded: Any = {"pad": [0] * 20_000}
    uncapped = STEPS_BASE + STEPS_PER_VALUE * 20_002
    assert steps(padded) == STEPS_MAX == 120_000
    assert len(_required(STEPS_MAX - 1).failures(padded)) == STEPS_MAX - 1
    assert _required(STEPS_MAX).failures(padded) == [Failure((), OUT_OF_STEPS)]
    assert steps(padded, WRITE_STEPS_MAX) == uncapped == 170_016
    assert len(_required(STEPS_MAX).failures(padded, ceiling=WRITE_STEPS_MAX)) == STEPS_MAX


def test_a_write_s_ceiling_is_the_budget_of_a_descriptor_s_most_values() -> None:
    assert WRITE_STEPS_MAX == STEPS_BASE + STEPS_PER_VALUE * MAX_VALUES == 1_610_000
    large: Any = [0] * (MAX_VALUES + 50_000)
    assert steps(large, WRITE_STEPS_MAX) == WRITE_STEPS_MAX


def _listed(members: int) -> tuple[dict[str, Any], list[Any]]:
    """An ordinary schema of an array of objects of string members, and ``MAX_LIST`` items."""
    names = [f"m{i}" for i in range(members)]
    item = {
        "type": "object",
        "additionalProperties": False,
        "required": names,
        "properties": {name: {"type": "string", "maxLength": 16} for name in names},
    }
    schema = {"type": "object", "properties": {"mark": {"type": "array", "items": item}}}
    return schema, [dict.fromkeys(names, "abc") for _ in range(MAX_LIST)]


def test_an_operator_s_write_of_list_limit_items_of_several_members_is_evaluated() -> None:
    """§14 lets a list hold 10,000 members: items of 8 members take about 220,000 steps, past a
    proposal's ceiling, whose value has at most 64 KiB, but within a write's."""
    schema, value = _listed(8)
    packs = registry(extension_schemas={"dataset": schema})
    assert check_writes(descriptors(value), packs, ceiling=WRITE_STEPS_MAX) == []
    broken = [*value[:-1], {"m0": "abc"}]
    found = check_writes(descriptors(broken), packs, ceiling=WRITE_STEPS_MAX)
    assert codes(found) == [("INVALID_EXTENSION", f"/0/extensions/shelf/mark/{MAX_LIST - 1}")]
    [refusal] = check_writes(descriptors(value), packs)
    assert (refusal.code, refusal.path) == ("LIMIT_EXCEEDED", "/0/extensions/shelf")
    assert refusal.limit is not None
    assert refusal.limit.model_dump() == {"name": "extension_steps", "max": STEPS_MAX}
    assert "curation session" in refusal.model_dump_json()


def test_a_write_s_spent_budget_names_no_curation_session_as_the_remedy() -> None:
    schema = {
        "$defs": RECURSIVE_ANY_OF["$defs"],
        "type": "object",
        "properties": {"mark": {"$ref": "#/$defs/node"}},
    }
    packs = registry(extension_schemas={"dataset": schema})
    [refusal] = check_writes(descriptors(_nested(20)), packs, ceiling=WRITE_STEPS_MAX)
    assert (refusal.code, refusal.path) == ("LIMIT_EXCEEDED", "/0/extensions/shelf")
    assert "curation session" not in refusal.model_dump_json()


def test_a_scoped_check_evaluates_only_the_descriptors_it_names() -> None:
    """D247: a change or a proposal evaluates what it adds or changes; every extension is still
    checked against the dataset's ``packs``, which needs no schema."""
    column = build.column("t.a", "string").model_dump(mode="json")
    column["extensions"] = {"shelf": {"mark": "C"}}
    column["curation"]["/extensions/shelf/mark"] = column["curation"]["/label"]
    written = [*descriptors("C"), _ADAPTER.validate_python(column)]
    packs = registry(extension_schemas={"dataset": SCHEMA, "column": SCHEMA})
    assert codes(check_writes(written, packs)) == [
        ("INVALID_EXTENSION", "/0/extensions/shelf/mark"),
        ("INVALID_EXTENSION", "/1/extensions/shelf/mark"),
    ]
    assert codes(check_writes(written, packs, only={"t.a"})) == [
        ("INVALID_EXTENSION", "/1/extensions/shelf/mark")
    ]
    assert check_writes(written, packs, only=set()) == []
    unlisted = [*descriptors(packs=()), written[1]]
    assert codes(check_writes(unlisted, None, only=set())) == [
        ("INVALID_EXTENSION", "/0/extensions/shelf"),
        ("INVALID_EXTENSION", "/1/extensions/shelf"),
    ]


def test_a_scoped_check_leaves_what_the_registry_decides_about_the_others_to_publish() -> None:
    """A dataset listing a pack the running registry lacks is refused by a full check, as a
    session's publish runs it, and passes one scoped to another descriptor (D247)."""
    written = [build.dataset(packs=["gone"]), build.table("members", ["member_id"])]
    assert codes(check_writes(written, None)) == [("INVALID_VALUE", "/0/fields/packs/0")]
    assert check_writes(written, None, only={"members"}) == []

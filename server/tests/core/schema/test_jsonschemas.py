"""A schema that names its dialect does not escape the step budget (SPEC D247, D392): evaluation
never leaves the module's counted validator, whatever ``$schema`` or ``$id`` a schema holds and
wherever, and registration resolves every reference to what evaluation resolves it to."""

import copy
import json
import random
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import jsonschema
import pytest
from referencing._core import Resolver

from aibi.core.schema.jsonschemas import (
    DIALECT,
    OUT_OF_STEPS,
    UNEVALUABLE,
    Checker,
    Failure,
    OutOfTime,
    TimedBudget,
    problems,
    steps,
)

_STOCK = (
    jsonschema.Draft3Validator,
    jsonschema.Draft4Validator,
    jsonschema.Draft6Validator,
    jsonschema.Draft7Validator,
    jsonschema.Draft201909Validator,
    jsonschema.Draft202012Validator,
)
_METASCHEMAS = (
    DIALECT,
    "https://json-schema.org/draft/2020-12/meta/core",
    "https://json-schema.org/draft/2020-12/meta/applicator",
    "https://json-schema.org/draft/2020-12/meta/validation",
    "https://json-schema.org/draft/2020-12/meta/unevaluated",
    "https://json-schema.org/draft/2019-09/schema",
    "http://json-schema.org/draft-07/schema#",
)
_DIALECTS = (
    DIALECT,
    "https://json-schema.org/draft/2019-09/schema",
    "http://json-schema.org/draft-07/schema#",
    "https://example.org/dialect",
)
# Absolute and relative, and colliding: under the root's https://example.org/dir/r, "a" and
# https://example.org/dir/a name the same resource.
_IDS = (*_METASCHEMAS, "https://example.org/dir/a", "a", "https://example.org/dir/", "b/c")
_ROOT_IDS = ("https://example.org/dir/r", "https://example.org/dir/", DIALECT)
_LEAVES: tuple[dict[str, Any], ...] = (
    {},
    {"type": "array"},
    {"type": "integer"},
    {"type": "object"},
    {"maxItems": 0},
    {"minItems": 1},
    {"uniqueItems": True},
    {"$ref": "#"},
    {"items": {"$ref": "#"}},
    {"properties": {"a": {"$ref": "#"}}},
)
_ONE = ("items", "not", "contains", "additionalProperties", "unevaluatedItems", "if", "zz")
_MANY = ("anyOf", "oneOf", "allOf", "prefixItems")
_NAMED = ("properties", "$defs", "dependentSchemas")
_KEYWORDS = (*_ONE, *_MANY, *_NAMED, "$ref", "maxItems", "type", "uniqueItems")
_PENDING = "pending"


class _EscapedError(Exception):
    """A stock validator of jsonschema's was built while a ``Checker`` evaluated."""


class _Tripwire:
    """Spies on jsonschema's stock validators: while armed, building one records its class and
    raises, so that an escape fails at once instead of running for hours."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.built: list[str] = []
        self._armed = False
        for stock in _STOCK:
            original = stock.__attrs_post_init__  # pyright: ignore[reportAttributeAccessIssue]

            def spy(self_: Any, _original: Any = original, _name: str = stock.__name__) -> None:
                if self._armed:
                    self.built.append(_name)
                    raise _EscapedError(_name)
                _original(self_)

            monkeypatch.setattr(stock, "__attrs_post_init__", spy)

    @contextmanager
    def armed(self) -> Iterator[None]:
        self._armed = True
        try:
            yield
        finally:
            self._armed = False


def _schema(rng: random.Random, depth: int, paths: list[list[str | int]], here: list[Any]) -> Any:
    """A random schema, with ``$schema`` and ``$id`` at random known positions and under an
    unknown keyword (``zz``), and references to be filled once every position is known."""
    if depth <= 0 or rng.random() < 0.25:
        return copy.deepcopy(rng.choice(_LEAVES))
    paths.append(here)
    schema: dict[str, Any] = {}
    for _ in range(rng.randrange(1, 4)):
        keyword = rng.choice(_KEYWORDS)
        if keyword in _ONE:
            schema[keyword] = _schema(rng, depth - 1, paths, [*here, keyword])
            if keyword == "if":
                schema["then"] = _schema(rng, depth - 1, paths, [*here, "then"])
        elif keyword in _MANY:
            schema[keyword] = [
                _schema(rng, depth - 1, paths, [*here, keyword, index])
                for index in range(rng.randrange(1, 3))
            ]
        elif keyword in _NAMED:
            names = rng.sample(["a", "b", "c"], rng.randrange(1, 3))
            schema[keyword] = {
                name: _schema(rng, depth - 1, paths, [*here, keyword, name]) for name in names
            }
        elif keyword == "$ref":
            schema["$ref"] = _PENDING
        elif keyword == "maxItems":
            schema["maxItems"] = rng.randrange(0, 3)
        elif keyword == "type":
            schema["type"] = rng.choice(["array", "object", "integer"])
        else:
            schema["uniqueItems"] = True
    drawn = rng.random()
    if drawn < 0.2:
        schema["$schema"] = rng.choice(_DIALECTS)
    elif drawn < 0.45:
        schema["$id"] = rng.choice(_IDS)
    elif drawn < 0.5:
        schema["$schema"] = rng.choice(_DIALECTS)
        schema["$id"] = rng.choice(_IDS)
    return schema


def _fill(node: Any, rng: random.Random, paths: list[list[str | int]]) -> None:
    if isinstance(node, dict):
        for key, value in list(node.items()):  # pyright: ignore[reportUnknownVariableType]
            if key == "$ref" and value == _PENDING:
                path = rng.choice(paths) if rng.random() < 0.85 else ["zz"]
                node[key] = "#" + "".join(f"/{token}" for token in path)
            else:
                _fill(value, rng, paths)
    elif isinstance(node, list):
        for item in node:  # pyright: ignore[reportUnknownVariableType]
            _fill(item, rng, paths)


def _value(rng: random.Random, depth: int) -> Any:
    if depth <= 0 or rng.random() < 0.3:
        return rng.choice([1, "a", None, [], {}, [1, 1], [{"a": 1}, {"a": 1}]])
    if rng.random() < 0.6:
        return [_value(rng, depth - 1) for _ in range(rng.randrange(0, 3))]
    names = rng.sample(["a", "b", "c"], rng.randrange(0, 3))
    return {name: _value(rng, depth - 1) for name in names}


def _corpus(seeds: range) -> Iterator[tuple[int, dict[str, Any], Any]]:
    """Seeded schemas, about half with the root's own ``$schema`` and a third with its ``$id``,
    and a value for each."""
    for seed in seeds:
        rng = random.Random(seed)
        paths: list[list[str | int]] = []
        schema: dict[str, Any] = _schema(rng, 4, paths, [])
        if not isinstance(schema, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            schema = {"items": schema}
        if rng.random() < 0.5:
            schema["$schema"] = DIALECT
        if rng.random() < 0.3:
            schema["$id"] = rng.choice(_ROOT_IDS)
        _fill(schema, rng, paths)
        yield seed, schema, _value(rng, 6)


def _failures(schema: dict[str, Any], value: Any) -> list[Failure] | str:
    budget = TimedBudget(steps(value), time.monotonic() + 5)
    try:
        return Checker(schema).failures(value, budget=budget)
    except OutOfTime:
        return "out of time"


def test_evaluation_never_builds_a_stock_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """The class (D392): over seeded schemas with ``$schema`` and ``$id`` anywhere (the bundled
    metaschemas' URIs, other dialects, absolute, relative and colliding ids), evaluating any of
    them, accepted or not, builds none of jsonschema's stock validators, which spend no step and
    look at no deadline."""
    escaped: list[tuple[int, str]] = []
    accepted = refused = 0
    tripwire = _Tripwire(monkeypatch)
    for seed, schema, value in _corpus(range(1500)):
        given = copy.deepcopy(schema)
        if problems(schema):
            refused += 1
        else:
            accepted += 1
        with tripwire.armed():
            try:
                _failures(schema, value)
            except _EscapedError as escape:
                escaped.append((seed, str(escape)))
        assert schema == given, seed
    assert escaped == []
    assert tripwire.built == []
    assert accepted > 400, accepted
    assert refused > 600, refused


def test_a_root_schema_and_id_change_no_result() -> None:
    """The root's own ``$schema`` and ``$id`` are accepted and evaluation leaves them out."""
    compared = 0
    for seed, schema, value in _corpus(range(1500)):
        bare = {k: v for k, v in schema.items() if k not in ("$schema", "$id")}
        if problems(bare):
            continue
        compared += 1
        expected = _failures(bare, value)
        for root in (
            {"$schema": DIALECT},
            {"$id": _ROOT_IDS[0]},
            {"$schema": DIALECT, "$id": DIALECT},
        ):
            dressed = {**root, **bare}
            assert problems(dressed) == [], seed
            assert _failures(dressed, value) == expected, seed
    assert compared > 200


_PROBES = ([[1, [2]], [[]], {"a": [1]}], {"a": {"a": [1, {"b": 2}]}, "b": [[1]], "c": {"b": 1}})
_HAND: tuple[dict[str, Any], ...] = (
    {"$schema": DIALECT, "$id": "https://example.org/r", "items": {"$ref": "#"}},
    {"$id": DIALECT, "properties": {"a": {"$ref": "#/$defs/a"}}, "$defs": {"a": {"$ref": "#"}}},
    {"$id": "https://example.org/dir/r", "properties": {"a": {"$ref": "#/x"}}, "x": {"items": {}}},
    {"$schema": DIALECT, "$anchor": "top", "items": {"$ref": "#top"}},
    {"$defs": {"m": {"$dynamicAnchor": "meta"}}, "properties": {"a": {"$dynamicRef": "#meta"}}},
    {"properties": {"a": {"$ref": "#/x/y"}}, "x": {"y": {"items": {"$ref": "#/x/y"}}}},
    {"$id": "b/c", "$defs": {"x": {"$anchor": "an", "type": "string"}}, "items": {"$ref": "#an"}},
)


def test_registration_and_evaluation_resolve_every_reference_alike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every reference evaluation looks up resolves to the subschema registration resolved it
    to, the root being the same copy, without its ``$schema`` and ``$id``."""
    looked: list[tuple[str, object]] = []
    original = Resolver.lookup

    def lookup(self: Resolver[Any], ref: str) -> Any:
        resolved = original(self, ref)
        looked.append((ref, resolved.contents))
        return resolved

    monkeypatch.setattr(Resolver, "lookup", lookup)
    corpus = [(schema, [value, *_PROBES], False) for _, schema, value in _corpus(range(1000))]
    schemas = references = 0
    for schema, values, hand in [*corpus, *((schema, _PROBES, True) for schema in _HAND)]:
        looked.clear()
        if hand:
            assert problems(schema) == [], schema
        elif problems(schema):
            continue
        registered: dict[str, list[object]] = {}
        for ref, target in looked:
            registered.setdefault(ref, []).append(target)
        looked.clear()
        for value in values:
            _failures(schema, value)
        root = {k: v for k, v in schema.items() if k not in ("$schema", "$id")}
        for ref, target in looked:
            assert any(
                target is there or (target == root and there == root)
                for there in registered.get(ref, [])
            ), (schema, ref)
        schemas += bool(looked)
        references += len(looked)
    assert schemas > 80, schemas
    assert references > 300, references


D = DIALECT
_ROUND_3 = {
    "$schema": D,
    "anyOf": [{"items": {"$ref": "#"}, "maxItems": 0}, {"items": {"$ref": "#"}}],
}
_SUBSCHEMA = {
    "$defs": {
        "r": {
            "$schema": D,
            "anyOf": [
                {"items": {"$ref": "#/$defs/r"}, "maxItems": 0},
                {"items": {"$ref": "#/$defs/r"}},
            ],
        }
    },
    "$ref": "#/$defs/r",
}
_METASCHEMA_ID = {"properties": {"x": {"$id": D, "items": {"$ref": "#"}}}}
_RECURSION = {
    "$schema": D,
    "anyOf": [{"items": {"$ref": "#/zz"}, "maxItems": 0}, {"items": {"$ref": "#/zz"}}],
}
_STRIPPED_ID = {
    "$id": "https://example.org/dir/r",
    "$ref": "#/$defs/s2",
    "$defs": {
        "s1": {"$id": "https://example.org/dir/a", "zz": {"type": "array"}},
        "s2": {"$id": "a", "items": {"$ref": "#/zz"}, "zz": _RECURSION},
    },
}
_STRIPPED_ID_TYPES = {
    "$id": "https://example.org/dir/r",
    "$ref": "#/$defs/s2",
    "$defs": {
        "s1": {"$id": "https://example.org/dir/a", "zz": {"type": "array"}},
        "s2": {"$id": "a", "zz": {"type": "string"}, "properties": {"v": {"$ref": "#/zz"}}},
    },
}


def _nested(depth: int) -> Any:
    value: Any = 1
    for _ in range(depth):
        value = [value]
    return value


_TIMED = """
import json, sys, time
from aibi.core.schema.jsonschemas import Checker, OutOfTime, TimedBudget
schema, value = json.loads(sys.argv[1])
started = time.monotonic()
found = [[list(f.path), f.keyword] for f in Checker(schema).failures(value)]
counted = time.monotonic() - started
started = time.monotonic()
try:
    Checker(schema).failures(value, budget=TimedBudget(10**9, time.monotonic() + 0.5))
    timed = "finished"
except OutOfTime:
    timed = "out of time"
print(json.dumps([found, counted, timed, time.monotonic() - started]))
"""


@pytest.mark.parametrize(
    ("schema", "value", "accepted"),
    [
        # Round 3's: the root, reached again through $ref "#", named its dialect (13.6 s at 18).
        (_ROUND_3, _nested(18), True),
        (_SUBSCHEMA, _nested(16), False),
        # A subschema's $id named a bundled metaschema, which "#" then resolved to (10.1 s).
        (_METASCHEMA_ID, {"x": [{"required": [{"a": i} for i in range(3000)]}]}, False),
        # The root's $id left out of evaluation but not of registration (2.18 s at 16, growing
        # fourfold every two levels).
        (_STRIPPED_ID, _nested(16), False),
    ],
)
def test_a_schema_naming_a_dialect_or_a_base_is_cut_off(
    schema: dict[str, Any], value: Any, accepted: bool
) -> None:
    """The escapes the reviews found, each in a process of its own so that a regression fails in
    seconds instead of hanging: evaluation spends its steps and honours a deadline."""
    assert (problems(schema) == []) is accepted
    ran = subprocess.run(
        [sys.executable, "-c", _TIMED, json.dumps([schema, value])],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr
    found, counted, timed, deadline = json.loads(ran.stdout)
    assert counted < 2, counted
    assert deadline < 2, deadline
    if schema is not _METASCHEMA_ID:
        assert found == [[[], OUT_OF_STEPS]]
        assert timed == "out of time"


_REQUIRED = {"required": [1, 1], "type": 5}
"""Valid in a schema of v1, and refused by jsonschema's metaschema in three ways."""


@pytest.mark.parametrize(
    ("schema", "value", "expected"),
    [
        # "#" under the $id is the subschema itself, whose items the object is not.
        (_METASCHEMA_ID, {"x": [_REQUIRED]}, []),
        # "#" under the $id is the subschema itself, a reference to itself.
        ({"$id": D, "items": {"$id": D, "$ref": "#"}}, [_REQUIRED], [Failure((), UNEVALUABLE)]),
        (
            {
                "items": {
                    "$id": "https://json-schema.org/draft/2020-12/meta/validation",
                    "$ref": "#",
                }
            },
            [_REQUIRED],
            [Failure((), UNEVALUABLE)],
        ),
    ],
)
def test_evaluation_never_reaches_jsonschemas_metaschemas(
    schema: dict[str, Any], value: Any, expected: list[Failure]
) -> None:
    """Even in a schema registration refuses, a reference under an ``$id`` that names a bundled
    metaschema resolves within the schema, never to the metaschema, which would refuse the value
    three ways: evaluation's registry holds the schema alone."""
    assert problems(schema)
    assert Checker(schema).failures(value) == expected


def test_unique_items_reached_through_the_root_is_linear() -> None:
    """``uniqueItems`` on a schema reached again through ``$ref: "#"`` from ``items``, under the
    root's own ``$schema``, is this module's linear check (10.1 s for 3,000 objects when
    jsonschema's stock validator compared every pair)."""
    schema = {
        "$schema": D,
        "anyOf": [
            {"type": "object"},
            {"type": "array", "uniqueItems": True, "items": {"$ref": "#"}},
        ],
    }
    assert problems(schema) == []
    checker = Checker(schema)
    started = time.monotonic()
    assert checker.failures([[{"a": i} for i in range(3000)]], ceiling=10**8) == []
    assert time.monotonic() - started < 1
    assert checker.failures([[{"a": 1}, {"a": 1.0}]]) == [Failure((), "anyOf")]

    # The stock validator sorts first and misses [1] and [1] around True, which Python orders as 1.
    equal_around_a_boolean = {
        "$schema": D,
        "anyOf": [
            {"type": "integer"},
            {"type": "boolean"},
            {"type": "array", "uniqueItems": True, "items": {"$ref": "#"}},
        ],
    }
    assert Checker(equal_around_a_boolean).failures([[[1], [True], [1]]]) == [Failure((), "anyOf")]


@pytest.mark.parametrize(
    "schema",
    [
        {"properties": {"a": {"$schema": D}}},
        {"items": {"$schema": D, "type": "string"}},
        {"properties": {"a": {"$schema": "https://example.org/dialect"}}},
        {"$defs": {"r": {"$schema": D}}},
        # A reference's target is checked wherever it is, under an unknown keyword included.
        {"properties": {"a": {"$ref": "#/x"}}, "x": {"$schema": D, "type": "string"}},
        {"properties": {"a": {"$ref": "#/x/y"}}, "x": {"y": {"$schema": D}}},
    ],
)
def test_a_subschema_declaring_a_schema_is_refused(schema: dict[str, Any]) -> None:
    assert problems(schema) == ["a subschema declares a $schema, which v1 does not use"]


@pytest.mark.parametrize(
    "schema",
    [
        {"properties": {"a": {"$id": "https://example.org/a"}}},
        {"items": {"$id": "a", "type": "string"}},
        {"$defs": {"r": {"$id": D}}},
        {"properties": {"a": {"$ref": "#/x"}}, "x": {"$id": "https://example.org/x"}},
        {"properties": {"a": {"$ref": "#/x/y"}}, "x": {"y": {"$id": D, "type": "string"}}},
        {"items": {"$id": ""}},
        {"items": {"$id": "#"}},
        _METASCHEMA_ID,
    ],
)
def test_a_subschema_declaring_an_id_is_refused(schema: dict[str, Any]) -> None:
    assert problems(schema) == ["a subschema declares an $id, which v1 does not use"]


def test_a_root_id_with_subschema_ids_is_refused_whatever_they_resolve_to() -> None:
    """Under the root's $id, "a" and https://example.org/dir/a name the same resource, which
    registration and evaluation would resolve differently were the root's $id left out of only
    one ({"v": []} was valid on the base and invalid under that strip)."""
    assert problems(_STRIPPED_ID_TYPES) == ["a subschema declares an $id, which v1 does not use"]
    assert problems(_STRIPPED_ID) == [
        "a subschema declares a $schema, which v1 does not use",
        "a subschema declares an $id, which v1 does not use",
    ]


@pytest.mark.parametrize(
    "schema",
    [
        {"$schema": D, "$id": "https://example.org/r", "type": "object"},
        {"$id": "r.schema.json", "items": {"$ref": "#"}},
        {"$id": D, "items": {"$ref": "#"}},
        {"$anchor": "top", "items": {"$ref": "#top"}},
        {"$dynamicAnchor": "top", "items": {"$dynamicRef": "#top"}},
        {
            "properties": {"$schema": {"type": "string"}, "$id": {"type": "string"}},
            "required": ["$schema"],
        },
        {
            "$defs": {"$schema": {"type": "string"}},
            "properties": {"a": {"$ref": "#/$defs/$schema"}},
        },
        {"enum": [{"$schema": D, "$id": "a"}], "default": {"$id": "b"}},
    ],
)
def test_a_root_schema_and_id_and_names_that_are_not_keywords_are_accepted(
    schema: dict[str, Any],
) -> None:
    given = copy.deepcopy(schema)
    assert problems(schema) == []
    Checker(schema)
    assert schema == given


@pytest.mark.parametrize(
    ("schema", "problem"),
    [
        ({"$id": 5}, "it is not a valid schema (type at /$id)"),
        ({"$id": "#frag"}, "it is not a valid schema (pattern at /$id)"),
        ({"$id": "a#b"}, "it is not a valid schema (pattern at /$id)"),
        ({"$schema": D + "#"}, f"it declares a dialect other than {D}"),
    ],
)
def test_an_invalid_root_id_or_dialect_is_refused(schema: dict[str, Any], problem: str) -> None:
    """The root's own $schema and $id are left out of evaluation, not out of registration."""
    assert problems(schema) == [problem]


def test_a_relative_root_id_does_not_hide_the_schemas_anchors() -> None:
    """The base refused this ("resolves to nothing"): it crawled the root under urljoin("b/c",
    "b/c"), which is "b/b/c", and looked its anchors up under "b/c"."""
    schema = {
        "$id": "b/c",
        "$defs": {"x": {"$anchor": "an", "type": "string"}},
        "items": {"$ref": "#an"},
    }
    assert problems(schema) == []
    checker = Checker(schema)
    assert checker.failures(["a", "b"]) == []
    assert checker.failures([1]) == [Failure((0,), "type")]


def test_schema_and_id_as_property_names_are_validated_as_members() -> None:
    checker = Checker(
        {
            "$schema": D,
            "properties": {"$schema": {"type": "string"}, "$id": {"type": "string"}},
            "required": ["$schema"],
        }
    )
    assert checker.failures({"$schema": "x", "$id": "y"}) == []
    assert checker.failures({"$id": 1}) == [Failure((), "required"), Failure(("$id",), "type")]


def test_problems_and_checker_leave_their_argument_unchanged() -> None:
    schema = {
        "$schema": D,
        "$id": "https://example.org/r",
        "properties": {"a": {"$ref": "#"}, "b": {"$schema": D, "$id": "b"}},
    }
    given = copy.deepcopy(schema)
    assert problems(schema)
    assert schema == given
    Checker(schema).failures({"a": {"a": 1}, "b": 2})
    assert schema == given
    accepted = {"$schema": D, "$id": "https://example.org/r", "items": {"$ref": "#"}}
    given = copy.deepcopy(accepted)
    assert problems(accepted) == []
    Checker(accepted).failures([[[]]])
    assert accepted == given

# jsonschema's stubs leave ``iter_errors`` partly unknown (one overload takes an untyped
# instance), and referencing's resources are generic over untyped contents; the types this
# module exposes are its own.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
"""Pack extension schemas: the only module that calls jsonschema (SPEC §10.1, D247).

A pack's schema for a descriptor kind is JSON Schema 2020-12. It is checked when the pack is
registered (``problems``):

- it must be a valid schema of that dialect, and no subschema declares another;
- every ``$ref`` and ``$dynamicRef`` in it is local (``#…``), so that validating an extension
  object never retrieves anything, and resolves within the schema, to a valid schema;
- what a reference resolves to is checked as every subschema is, wherever it is in the schema
  (under a keyword the dialect does not know, say), and so is everything it refers to;
- no chain of references and in-place applicators (``allOf``, ``anyOf``, ``oneOf``, ``not``,
  ``if``, ``then``, ``else``, ``dependentSchemas``) leads from a subschema back to itself, since
  evaluating one would never descend into the instance (``{"$ref": "#"}``);
- it has no ``pattern`` or ``patternProperties``: v1 has no regular expression engine that runs
  in linear time, and a pattern would run on values from imported files and agents.

Validation (``Checker``) runs on every descriptor write with an empty registry of resources, and
formats are annotations, never asserted. ``uniqueItems`` is checked in linear time, by the RFC 8785
bytes of each item, rather than by jsonschema's comparison of every pair: the equality is JSON
Schema's (``1`` equals ``1.0``, booleans are not numbers, objects compare by content), and a pack
schema could otherwise make one extension value of ``MAX_LIST`` objects cost minutes. jsonschema's
own check sorts the items first and so misses equal items where Python orders a boolean and a
number as equal (``[[1], [true], [1]]``); this one does not. An error is reported by where it is in
the extension object and the keyword that failed, never by the value that failed it (A6).

``unevaluatedItems`` and ``unevaluatedProperties`` are jsonschema's, with the items and members that
other keywords evaluated collected in a set rather than a list, since jsonschema's membership test
against its list makes one array of ``MAX_LIST`` items cost seconds; the results are jsonschema's.
``anyOf`` and ``oneOf`` stop evaluating a subschema at its first error and keep none, and no error
repeats the value it is about (values are validated as copies whose ``repr`` is fixed): jsonschema's
messages hold the whole value, and its ``anyOf`` keeps every error of every subschema, so memory
would grow with the value times the errors. The results are jsonschema's.

Evaluating one extension object takes at most ``STEPS_BASE`` plus ``STEPS_PER_VALUE`` per JSON value
of the object, and never more than a ceiling, steps (``steps``): ``STEPS_MAX`` for a proposal, whose
value has at most ``MAX_PROPOSAL_BYTES``, and ``WRITE_STEPS_MAX``, the budget of an object of
``MAX_VALUES`` values, the most a descriptor holds, for what an operator or an importer writes. A
keyword evaluation and an error spend a step each, and so does every ``THROUGH_PER_STEP`` items,
members or values a keyword goes through itself, whatever its subschemas spend (``items``,
``contains``, ``additionalProperties`` and ``propertyNames`` over ``true``, the values
``uniqueItems`` writes, the items and members that ``unevaluatedItems`` and
``unevaluatedProperties`` look at). The budget does not grow with the schema, so neither a schema
of many keywords nor a value padded where the schema does not look buys recursion more room. A
schema that reaches each value a few times stays well within it: an ordinary schema of an array of
objects of string members (``type``, ``additionalProperties``, ``required``, and ``type`` and
``maxLength`` for each member) takes 2.5 to 3 steps per value, so ``MAX_LIST`` items of 3 members
take about 100,000 steps and of 8 members about 220,000, while recursion under ``anyOf``,
``oneOf``, ``not`` or ``allOf`` would otherwise evaluate the same values again at every level, in
time exponential in the value's depth. ``STEPS_MAX`` is about 0.8 s of the slowest steps (recursion
through references) and ``WRITE_STEPS_MAX`` about 11 s, measured, and memory stays at about a MiB,
since no error keeps a copy of the value and ``anyOf`` keeps no error. A value whose budget is spent
gives the failure ``OUT_OF_STEPS``; one the schema cannot evaluate otherwise (a recursion too deep,
a reference that does not resolve) gives ``UNEVALUABLE``; neither raises.

Schemas whose cost grows with their own depth or with the value's depth whatever the budget are an
accepted limit, since a pack's author writes them and registration is not an agent's: nested
``anyOf`` under ``unevaluatedProperties``, a ``contains`` over hundreds of branches, and recursion
through an in-place applicator under ``unevaluatedProperties``
(``{"allOf": [{"properties": {"child": {"$ref": …}}}], "unevaluatedProperties": false}``), which
evaluates each level twice, once to validate it and once to find what it evaluated, so that the work
doubles with each level of the value and an ordinary value 10 deep spends its budget (jsonschema
itself takes seconds at 16 deep).
"""

from collections.abc import Callable, Iterable, Iterator, Mapping, Sized
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, cast

from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import JsonValue
from referencing import Registry, Resource
from referencing._core import Resolver
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012, Schema

from aibi.core.schema.jsonio import JsonError, canonical
from aibi.core.schema.limits import MAX_VALUES

DIALECT = "https://json-schema.org/draft/2020-12/schema"
UNEVALUABLE = "unevaluable"
"""The keyword of a failure where the schema could not evaluate the value."""
OUT_OF_STEPS = "out_of_steps"
"""The keyword of a failure where evaluating the value spent its step budget (D247)."""
_REFERENCES = ("$ref", "$dynamicRef")
_REFUSED_KEYWORDS = ("pattern", "patternProperties")
_IN_PLACE_LISTS = ("allOf", "anyOf", "oneOf")
_IN_PLACE = ("not", "if", "then", "else")
STEPS_BASE = 10_000
STEPS_PER_VALUE = 8
"""Steps per JSON value of the extension object, on top of ``STEPS_BASE`` (D247)."""
STEPS_MAX = 120_000
"""The most steps one extension object of a proposal takes, whatever its size (D247)."""
WRITE_STEPS_MAX = STEPS_BASE + STEPS_PER_VALUE * MAX_VALUES
"""The most steps one extension object an operator or an importer writes takes: the budget of an
object of ``MAX_VALUES`` values, the most a descriptor holds (D247)."""
THROUGH_PER_STEP = 4
"""Items or members a keyword goes through itself per step (D247)."""


def _remote(node: JsonValue) -> bool:
    """Whether the schema holds a reference that is not local."""
    if isinstance(node, list):
        return any(_remote(item) for item in node)
    if not isinstance(node, dict):
        return False
    for key, value in node.items():
        if key in _REFERENCES and isinstance(value, str) and not value.startswith("#"):
            return True
        if _remote(value):
            return True
    return False


Located = tuple[Schema, Resolver[Schema]]


def _subschemas(resource: Resource[Schema], resolver: Resolver[Schema]) -> Iterator[Located]:
    """Every subschema of a resource, the resource's own contents first, each with the resolver
    that resolves its references."""
    yield resource.contents, resolver
    for sub in resource.subresources():
        yield from _subschemas(sub, resolver.in_subresource(sub))


def _in_place(contents: Schema, resolver: Resolver[Schema]) -> Iterator[Located]:
    """The subschemas a subschema applies to the same instance: its in-place applicators and
    what its references resolve to, which ``_structure`` has checked resolve."""
    if not isinstance(contents, Mapping):
        return
    children: list[Schema] = []
    for key in _IN_PLACE_LISTS:
        found = contents.get(key)
        if isinstance(found, list):
            children.extend(found)
    children.extend(contents[key] for key in _IN_PLACE if key in contents)
    dependent = contents.get("dependentSchemas")
    if isinstance(dependent, Mapping):
        children.extend(dependent.values())
    for child in children:
        yield child, resolver.in_subresource(DRAFT202012.create_resource(child))
    for key in _REFERENCES:
        reference = contents.get(key)
        if isinstance(reference, str):
            resolved = resolver.lookup(reference)
            yield resolved.contents, resolved.resolver


def _cycles(start: Located) -> bool:
    """Whether a chain of in-place subschemas leads from one back to itself."""
    done: set[int] = set()
    path: set[int] = set()
    stack: list[tuple[int, Iterator[Located]]] = []

    def enter(located: Located) -> bool:
        key = id(located[0])
        if key in path:
            return True
        if key not in done:
            path.add(key)
            stack.append((key, _in_place(*located)))
        return False

    if enter(start):
        return True
    while stack:
        key, successors = stack[-1]
        following = next(successors, None)
        if following is None:
            stack.pop()
            path.discard(key)
            done.add(key)
        elif enter(following):
            return True
    return False


def _structure(schema: Mapping[str, JsonValue]) -> list[str]:
    """What is wrong with a valid schema's subschemas and references: every subschema, and
    everything a reference resolves to, wherever it is, and what that refers to in turn."""
    found: set[str] = set()
    resource = DRAFT202012.create_resource(dict(schema))
    resolver = Registry().resolver_with_root(resource)
    pending = list(_subschemas(resource, resolver))
    known = {id(contents) for contents, _ in pending}
    seen: set[int] = set()
    while pending:
        contents, scoped = pending.pop()
        if id(contents) in seen or not isinstance(contents, Mapping):
            continue
        seen.add(id(contents))
        declared = contents.get("$schema")
        if declared is not None and declared != DIALECT:
            found.add(f"a subschema declares a dialect other than {DIALECT}")
        for keyword in _REFUSED_KEYWORDS:
            if keyword in contents:
                found.add(f"it uses {keyword}, which v1 does not evaluate")
        for key in _REFERENCES:
            reference = contents.get(key)
            if not isinstance(reference, str):
                continue
            try:
                resolved = scoped.lookup(reference)
            except (Unresolvable, ValueError):
                # referencing raises ValueError for a token that is not an index into an array.
                found.add(f"it holds a {key} that resolves to nothing in the schema")
                continue
            target = cast(object, resolved.contents)  # a pointer may land on any JSON value
            if id(target) in known or id(target) in seen:
                continue
            if not isinstance(target, Mapping | bool):
                found.add(f"it holds a {key} that resolves to something that is not a schema")
                continue
            try:
                Draft202012Validator.check_schema(target)
            except SchemaError:
                found.add(f"it holds a {key} that resolves to something that is not a valid schema")
                continue
            pending.extend(
                _subschemas(DRAFT202012.create_resource(resolved.contents), resolved.resolver)
            )
    if found:
        return sorted(found)
    for located in _subschemas(resource, resolver):
        if _cycles(located):
            return ["it refers to itself without descending into the value"]
    return []


def problems(schema: Mapping[str, JsonValue]) -> list[str]:
    """What keeps ``schema`` from being a pack's extension schema; empty when it is one."""
    found: list[str] = []
    declared = schema.get("$schema")
    if declared is not None and declared != DIALECT:
        found.append(f"it declares a dialect other than {DIALECT}")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        where = "/".join(str(token) for token in error.absolute_path)
        found.append(f"it is not a valid schema ({error.validator} at /{where})")
    if _remote(dict(schema)):
        found.append("it holds a $ref or $dynamicRef that is not local (#…)")
    if found:
        return found
    return _structure(schema)


@dataclass(frozen=True)
class Failure:
    """Where an extension object fails its schema, and the keyword that fails it
    (``UNEVALUABLE`` when the schema could not evaluate it)."""

    path: tuple[str | int, ...]
    keyword: str


class _OutOfStepsError(Exception):
    """The step budget of one evaluation is spent."""


class _Budget:
    """Steps left in one evaluation; a negative budget is unlimited."""

    __slots__ = ("left",)

    def __init__(self, steps: int) -> None:
        self.left = steps

    def spend(self, steps: int = 1) -> None:
        if self.left < 0:
            return
        if steps > self.left:
            raise _OutOfStepsError
        self.left -= steps


_BUDGET: ContextVar[_Budget | None] = ContextVar("_BUDGET", default=None)
_UNLIMITED = -1


def _budget() -> _Budget:
    return _BUDGET.get() or _Budget(_UNLIMITED)


def _through(values: int) -> int:
    """The steps of going through ``values`` items, members or values without evaluating a
    keyword on each, which costs about a quarter of a keyword evaluation."""
    return values // THROUGH_PER_STEP


class _Quiet(dict[str, Any]):
    """An object being validated, whose ``repr`` does not repeat it."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "{…}"


class _QuietList(list[Any]):
    """An array being validated, whose ``repr`` does not repeat it."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "[…]"


class _QuietStr(str):
    """A string being validated, or a member's name, whose ``repr`` does not repeat it."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "'…'"


def _quiet_one(value: object) -> object:
    if isinstance(value, str):
        return _QuietStr(value)
    if isinstance(value, dict):
        return _Quiet()
    if isinstance(value, list):
        return _QuietList()
    return value


def _quiet(value: JsonValue) -> object:
    """A copy of a value whose objects, arrays and strings have a fixed ``repr``, so that
    jsonschema's messages, which repeat the value they are about, cost nothing. Iterative, since a
    value may be nested deeper than Python recurses."""
    top = _quiet_one(value)
    pending: list[tuple[object, object]] = [(value, top)]
    while pending:
        source, target = pending.pop()
        if isinstance(source, dict) and isinstance(target, _Quiet):
            for key, member in cast(dict[str, object], source).items():
                target[_QuietStr(key)] = copied = _quiet_one(member)
                pending.append((member, copied))
        elif isinstance(source, list) and isinstance(target, _QuietList):
            for item in cast(list[object], source):
                copied = _quiet_one(item)
                target.append(copied)
                pending.append((item, copied))
    return top


def _count(node: object) -> int:
    """JSON values in a value, itself included; iterative, as ``_quiet`` is."""
    found = 0
    pending: list[object] = [node]
    while pending:
        current = pending.pop()
        found += 1
        if isinstance(current, dict):
            pending.extend(cast(dict[str, object], current).values())
        elif isinstance(current, list):
            pending.extend(cast(list[object], current))
    return found


_JSONSCHEMA_UNIQUE_ITEMS = Draft202012Validator.VALIDATORS["uniqueItems"]


def _unique_items(
    validator: Any, unique: object, instance: object, schema: object
) -> Iterator[ValidationError]:
    """``uniqueItems`` in linear time: items are equal when their RFC 8785 bytes are, which is
    JSON Schema's equality on JSON values (``1`` and ``1.0`` equal, ``true`` not ``1``, objects by
    content); a value that has no RFC 8785 form, which no descriptor holds, is left to
    jsonschema. Each value written spends a step."""
    if not unique or not validator.is_type(instance, "array"):
        return
    _budget().spend(_through(_count(instance)))
    seen: set[bytes] = set()
    for item in cast(list[JsonValue], instance):
        try:
            written = canonical(item)
        except JsonError:
            yield from _JSONSCHEMA_UNIQUE_ITEMS(validator, unique, instance, schema)
            return
        if written in seen:
            yield ValidationError("the array has items that are equal")
            return
        seen.add(written)


def _valid(validator: Any, instance: object, subschema: object) -> bool:
    """Whether ``instance`` is valid under a subschema applied in place, stopping at the first
    error."""
    return next(validator.descend(instance, subschema), None) is None


def _any_of(
    validator: Any, any_of: list[object], instance: object, schema: object
) -> Iterator[ValidationError]:
    """jsonschema's ``anyOf``, keeping no error of the subschemas."""
    if not any(_valid(validator, instance, subschema) for subschema in any_of):
        yield ValidationError("the value is valid under none of the subschemas")


def _one_of(
    validator: Any, one_of: list[object], instance: object, schema: object
) -> Iterator[ValidationError]:
    """jsonschema's ``oneOf``, keeping no error of the subschemas."""
    subschemas = iter(one_of)
    if not any(_valid(validator, instance, subschema) for subschema in subschemas):
        yield ValidationError("the value is valid under none of the subschemas")
    elif any(_valid(validator, instance, subschema) for subschema in subschemas):
        yield ValidationError("the value is valid under more than one of the subschemas")


def _resolved(validator: Any, reference: str) -> tuple[Any, Schema]:
    resolved = validator._resolver.lookup(reference)
    return validator.evolve(
        schema=resolved.contents, _resolver=resolved.resolver
    ), resolved.contents


class _Items:
    """The items of an array that keywords evaluated: the first ``prefix``, and ``indexes``."""

    __slots__ = ("indexes", "prefix")

    def __init__(self) -> None:
        self.prefix = 0
        self.indexes: set[int] = set()


def _evaluated_items(validator: Any, instance: list[object], schema: Any, found: _Items) -> None:
    """jsonschema's ``find_evaluated_item_indexes_by_schema``, adding to ``found``; each call and
    each item it looks at spend a step."""
    budget = _budget()
    budget.spend()
    if validator.is_type(schema, "boolean"):
        return
    if "items" in schema:
        found.prefix = len(instance)
        return
    for key in _REFERENCES:
        reference = schema.get(key)
        if reference is not None:
            located, contents = _resolved(validator, reference)
            _evaluated_items(located, instance, contents, found)
    if "prefixItems" in schema:
        found.prefix = max(found.prefix, len(schema["prefixItems"]))
    if "if" in schema:
        if validator.evolve(schema=schema["if"]).is_valid(instance):
            _evaluated_items(validator, instance, schema["if"], found)
            if "then" in schema:
                _evaluated_items(validator, instance, schema["then"], found)
        elif "else" in schema:
            _evaluated_items(validator, instance, schema["else"], found)
    for keyword in ("contains", "unevaluatedItems"):
        if keyword in schema:
            budget.spend(_through(len(instance)))
            checker = validator.evolve(schema=schema[keyword])
            found.indexes.update(
                index for index, item in enumerate(instance) if checker.is_valid(item)
            )
    for keyword in _IN_PLACE_LISTS:
        for subschema in schema.get(keyword, ()):
            if _valid(validator, instance, subschema):
                _evaluated_items(validator, instance, subschema, found)


def _unevaluated_items(
    validator: Any, unevaluated: object, instance: object, schema: object
) -> Iterator[ValidationError]:
    """jsonschema's ``unevaluatedItems``, in time linear in the array."""
    if not validator.is_type(instance, "array"):
        return
    items = cast(list[object], instance)
    found = _Items()
    _evaluated_items(validator, items, schema, found)
    _budget().spend(_through(len(items)))
    if any(index not in found.indexes for index in range(found.prefix, len(items))):
        yield ValidationError("the array has items that no keyword evaluated")


def _evaluated_keys(
    validator: Any, instance: dict[str, object], schema: Any, found: set[str]
) -> None:
    """jsonschema's ``find_evaluated_property_keys_by_schema``, adding to ``found``; each call and
    each member it looks at spend a step. ``patternProperties`` is refused at registration."""
    budget = _budget()
    budget.spend()
    if validator.is_type(schema, "boolean"):
        return
    for key in _REFERENCES:
        reference = schema.get(key)
        if reference is not None:
            located, contents = _resolved(validator, reference)
            _evaluated_keys(located, instance, contents, found)
    properties = schema.get("properties")
    if validator.is_type(properties, "object"):
        found.update(name for name in properties if name in instance)
    for keyword in ("additionalProperties", "unevaluatedProperties"):
        subschema = schema.get(keyword)
        if subschema is None:
            continue
        budget.spend(_through(len(instance)))
        found.update(
            name for name, member in instance.items() if _valid(validator, member, subschema)
        )
    for name, subschema in schema.get("dependentSchemas", {}).items():
        if name in instance:
            _evaluated_keys(validator, instance, subschema, found)
    for keyword in _IN_PLACE_LISTS:
        for subschema in schema.get(keyword, ()):
            if _valid(validator, instance, subschema):
                _evaluated_keys(validator, instance, subschema, found)
    if "if" in schema:
        if validator.evolve(schema=schema["if"]).is_valid(instance):
            _evaluated_keys(validator, instance, schema["if"], found)
            if "then" in schema:
                _evaluated_keys(validator, instance, schema["then"], found)
        elif "else" in schema:
            _evaluated_keys(validator, instance, schema["else"], found)


def _unevaluated_properties(
    validator: Any, unevaluated: object, instance: object, schema: object
) -> Iterator[ValidationError]:
    """jsonschema's ``unevaluatedProperties``, in time linear in the object: one error for the
    members no keyword evaluated that fail it."""
    if not validator.is_type(instance, "object"):
        return
    members = cast(dict[str, object], instance)
    found: set[str] = set()
    _evaluated_keys(validator, members, schema, found)
    _budget().spend(_through(len(members)))
    for name, member in members.items():
        if name in found:
            continue
        errors = validator.descend(member, unevaluated, path=name, schema_path=name)
        if next(errors, None) is not None:
            yield ValidationError("the object has members that no keyword evaluated")
            return


Keyword = Callable[[Any, Any, Any, Any], Iterable[ValidationError] | None]
_GOES_THROUGH: dict[str, str] = {
    "items": "array",
    "contains": "array",
    "additionalProperties": "object",
    "propertyNames": "object",
}
"""The keywords of jsonschema's that go through every item or member of the value themselves,
whatever their subschemas spend, and the type they do it for."""


def _counted(name: str, keyword: Keyword) -> Keyword:
    """The keyword's function, spending a step of the evaluation's budget on the call, on each
    error it yields, and on each item or member it goes through itself."""
    through = _GOES_THROUGH.get(name)

    def counted(
        validator: Any, value: object, instance: object, schema: object
    ) -> Iterator[ValidationError]:
        budget = _budget()
        width = 0
        if through is not None and validator.is_type(instance, through):
            width = len(cast(Sized, instance))
        budget.spend(1 + _through(width))
        for error in keyword(validator, value, instance, schema) or ():
            budget.spend()
            yield error

    return counted


_KEYWORDS: dict[str, Keyword] = {
    **Draft202012Validator.VALIDATORS,
    "uniqueItems": _unique_items,
    "anyOf": _any_of,
    "oneOf": _one_of,
    "unevaluatedItems": _unevaluated_items,
    "unevaluatedProperties": _unevaluated_properties,
}
_Validator = validators.extend(
    Draft202012Validator, {name: _counted(name, keyword) for name, keyword in _KEYWORDS.items()}
)


def steps(value: JsonValue, ceiling: int = STEPS_MAX) -> int:
    """The step budget of evaluating ``value``: ``STEPS_BASE`` plus ``STEPS_PER_VALUE`` per JSON
    value in it, at most ``ceiling`` (D247)."""
    return min(ceiling, STEPS_BASE + STEPS_PER_VALUE * _count(value))


class StepBudget(_Budget):
    """Steps that several evaluations share, such as those of a document's pack leaves (D285):
    each spends what it takes from what the ones before it left."""

    __slots__ = ()

    def __init__(self, steps: int) -> None:
        super().__init__(max(0, steps))


class Checker:
    """Validates extension objects against one schema that ``problems`` accepted."""

    def __init__(self, schema: Mapping[str, JsonValue]) -> None:
        self._validator = _Validator(dict(schema), registry=Registry())

    def failures(
        self, value: JsonValue, *, ceiling: int = STEPS_MAX, budget: StepBudget | None = None
    ) -> list[Failure]:
        """Where ``value`` fails the schema, within ``steps(value, ceiling)`` steps, or within
        what is left of ``budget``, which the evaluation spends."""
        token = _BUDGET.set(_Budget(steps(value, ceiling)) if budget is None else budget)
        try:
            found = [
                Failure(
                    tuple(str(t) if isinstance(t, str) else t for t in error.absolute_path),
                    str(error.validator),
                )
                for error in self._validator.iter_errors(_quiet(value))
            ]
        except _OutOfStepsError:
            return [Failure((), OUT_OF_STEPS)]
        except (RecursionError, Unresolvable):
            return [Failure((), UNEVALUABLE)]
        finally:
            _BUDGET.reset(token)
        return sorted(found, key=lambda failure: ([str(t) for t in failure.path], failure.keyword))


__all__ = [
    "DIALECT",
    "OUT_OF_STEPS",
    "STEPS_BASE",
    "STEPS_MAX",
    "STEPS_PER_VALUE",
    "THROUGH_PER_STEP",
    "UNEVALUABLE",
    "WRITE_STEPS_MAX",
    "Checker",
    "Failure",
    "StepBudget",
    "problems",
    "steps",
]

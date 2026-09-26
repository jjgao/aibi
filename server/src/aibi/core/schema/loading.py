"""Loading documents and descriptors: parse, substitute ``params``, validate, check (SPEC §7.1).

Every problem becomes a refusal whose path points into the document as written (§8.6). Where a
problem lies inside a value that a parameter supplied, the path points at the ``"$name"`` string
and the message names the parameter.

Loading goes on past ``null`` members and refused parameter references, so that independent
problems are all reported, but nothing is reported under a position already refused, nor what
follows only from it: a clause whose ``kind`` was refused is not checked; an object that lost a
null member is not refused for lacking that member; and the checks that need a cohort's
datasets, its ``unmapped`` or a view's cohorts skip what a null left unknown. Refusals are
de-duplicated by (path, code), sorted, and capped at ``MAX_REFUSALS``; a last refusal says how
many were left out.

Positions are tuples of JSON Pointer tokens, and a pointer is written only for a refusal
returned. Pydantic copies the keys above a problem into its location, so the paths to values are
capped (§14): at parse time, and again when parameters are substituted.
"""

import gc
import heapq
import json
import os
import threading
import types
from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import combinations
from typing import Annotated, Any, Literal, Union, cast, get_args, get_origin

from pydantic import BaseModel, Discriminator, JsonValue, Tag, TypeAdapter, ValidationError
from pydantic_core import ErrorDetails

from aibi.core.schema.checks import Unknown, check_document
from aibi.core.schema.descriptors import (
    PARENT_SCOPE_KINDS,
    DescModel,
    Descriptor,
    parent_scope_kind,
)
from aibi.core.schema.document import (
    CORE_KINDS_TEXT,
    PARSED,
    PREDICATE_MEMBERS,
    Document,
    clause_tag,
    predicate_holds,
)
from aibi.core.schema.jsonio import JsonError, escape_token, parse_json, pointer
from aibi.core.schema.limits import (
    CONSTANT_CHARACTERS,
    DESCRIPTOR_BYTES,
    DOCUMENT_BYTES,
    LIST_MEMBERS,
    MAX_CLAUSES,
    MAX_DEPTH,
    MAX_DOCUMENT_BYTES,
    MAX_REFUSALS,
    NESTING_DEPTH,
    REFUSALS,
    LimitName,
)
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.params import Position, Substitution, substitute
from aibi.core.schema.refusals import (
    Limit,
    Refusal,
    RefusalCode,
    blank_secrets,
    finish_refusals,
)

_KEY_LABEL = "[key]"
"""Pydantic's location element for a problem with a dict key rather than its value."""


@dataclass(frozen=True)
class DocumentResult:
    """A validated document, or the refusals that stop it."""

    document: Document | None
    refusals: list[Refusal]
    written: JsonValue = None
    params_used: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    params_unused: list[str] = field(default_factory=list[str])
    positions: dict[Position, str] = field(default_factory=dict[Position, str])
    """The pointer tokens of each position that a parameter filled -> the parameter's name."""


@dataclass(frozen=True, slots=True)
class _Found:
    """A refusal found, built only if it is among those returned, at the pointer it is given."""

    position: Position | None
    code: str
    build: Callable[[str | None], Refusal]


class _Positions:
    """Positions, with the one at or above a given position, found in its depth's steps."""

    _END = object()

    def __init__(self) -> None:
        self._root: dict[object, Any] = {}

    def add(self, position: Position, value: object = True) -> None:
        node = self._root
        for token in position:
            node = node.setdefault(str(token), {})
        node.setdefault(self._END, value)

    def above(self, position: Position) -> tuple[int, object] | None:
        """How many tokens the position at or above ``position`` has, and its value; or None."""
        node = self._root
        for depth, token in enumerate((*position, None)):
            if self._END in node:
                return depth, node[self._END]
            if token is None:
                return None
            child = node.get(str(token))
            if child is None:
                return None
            node = child
        return None  # pragma: no cover - the loop returns at the end of the position

    def covers(self, position: Position) -> bool:
        return self.above(position) is not None

    def filled(self) -> bool:
        """Whether any position was added."""
        return bool(self._root)


def load_document(source: str | bytes) -> DocumentResult:
    size = len(source.encode("utf-8", "surrogatepass") if isinstance(source, str) else source)
    if size > MAX_DOCUMENT_BYTES:
        return DocumentResult(
            None,
            [
                Refusal(
                    code=RefusalCode.LIMIT_EXCEEDED,
                    path=None,
                    message=[text("The document is too large")],
                    limit=Limit(name=DOCUMENT_BYTES, max=MAX_DOCUMENT_BYTES),
                )
            ],
        )
    try:
        written = parse_json(source)
    except JsonError as problem:
        limit = Limit(name=problem.limit[0], max=problem.limit[1]) if problem.limit else None
        return DocumentResult(
            None,
            [
                Refusal(
                    code=RefusalCode(problem.code),
                    path=problem.pointer,
                    message=[text(problem.message)],
                    limit=limit,
                )
            ],
        )
    if not isinstance(written, dict):
        return DocumentResult(
            None,
            [
                Refusal(
                    code=RefusalCode.WRONG_TYPE, path="", message=[text("A document is an object")]
                )
            ],
            written,
        )

    found: list[_Found] = []
    refused = _Positions()
    nulls = _nulls(written)
    # A null member is reported below; leaving it out lets its siblings be checked.
    dropped: dict[Position, set[str]] = {}
    cleaned = _without_null_members(written, dropped) if nulls else written
    substitution = substitute(cleaned, size)
    params_refused = any(problem.position == ("params",) for problem in substitution.problems)
    for position in nulls:
        refused.add(position)
        if not (params_refused and position[:1] == ("params",)):
            found.append(_Found(position, RefusalCode.NULL_NOT_ALLOWED, _null_refusal))
    for problem in substitution.problems:
        found.append(_Found(problem.position, problem.code, problem.build))
    for position in substitution.failed:
        refused.add(position)
    knock_ons = _knock_ons(written, nulls, dropped, substitution)
    for position in knock_ons.under:
        refused.add(position)

    document = _paused(_validated, substitution, nulls, knock_ons, refused, found)
    if document is not None:
        for refusal in check_document(document, knock_ons.unknown):
            position = None if refusal.path is None else _position(refusal.path)
            if position is None or not refused.covers(position):
                found.append(_Found(position, refusal.code, _given(refusal)))

    positions = _Positions()
    for position, name in substitution.positions.items():
        positions.add(position, name)
    refusals = _paused(_finish, found, positions)
    return DocumentResult(
        document if not refusals else None,
        refusals,
        written,
        substitution.used,
        substitution.unused,
        substitution.positions,
    )


class _Pause:
    """The loads that have paused the cyclic garbage collector, in every thread."""

    def __init__(self) -> None:
        self.generation = 0
        self.reset()

    def reset(self) -> None:
        # Reentrant: a signal handler may load while its thread holds the lock.
        self.lock = threading.RLock()
        self.depth = 0
        self.resume = False
        """Whether the collector was enabled when the first of them paused it; ``False`` once
        the last has ended, so that a pause cut short before it reads the collector enables
        nothing."""
        self.generation += 1
        """Pauses begun before a fork end nothing in its child, which starts a generation."""


_PAUSE = _Pause()


def _after_fork_in_child() -> None:
    """A forked child runs one thread. The loads other threads were running do not run in it;
    one the forking thread was running (a signal handler's fork) returns into it, but it began in
    the parent's generation, so its end is ignored. The child starts with no pause, a new lock,
    and the collector as it was before the loads began."""
    if _PAUSE.depth and _PAUSE.resume:
        gc.enable()
    _PAUSE.reset()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_in_child)


def _paused[**P, T](function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """``function(*args, **kwargs)`` with the cyclic garbage collector paused, for work that
    allocates without cycles.

    Pydantic can report hundreds of thousands of errors, and each becomes a few small objects.
    None of them is in a cycle, so reference counting frees them all, but the collector would scan
    them again and again as they accumulate, which more than doubles the time.

    The collector's state belongs to the process, so loads in several threads share one pause:
    the first to start records whether the collector was enabled, and the last to finish enables
    it again if it was, so a load never enables a collector that was disabled when the pause
    began. What another thread does to the collector while loads run is not kept.

    The pause begins inside the ``try`` whose ``finally`` ends it, in this frame, so that an
    exception raised anywhere in between (a signal handler's) still ends it; a context manager
    would leave a gap between entering and registering its exit. The pause is counted before the
    collector is disabled for the same reason, and ending it waits for the lock again if the wait
    is interrupted, then raises the interruption.
    """
    paused = False
    generation = 0
    try:
        with _PAUSE.lock:
            _PAUSE.depth += 1
            paused = True
            generation = _PAUSE.generation
            if _PAUSE.depth == 1:
                _PAUSE.resume = gc.isenabled()
                gc.disable()
        return function(*args, **kwargs)
    finally:
        if paused:
            _end_pause(generation)


def _end_pause(generation: int) -> None:
    interrupted: BaseException | None = None
    while True:
        try:
            _PAUSE.lock.acquire()
            break
        except BaseException as error:  # a signal handler's, while waiting: wait again
            interrupted = error
    try:
        if generation == _PAUSE.generation:
            _PAUSE.depth -= 1
            if not _PAUSE.depth:
                if _PAUSE.resume:
                    gc.enable()
                _PAUSE.resume = False
    finally:
        _PAUSE.lock.release()
    if interrupted is not None:
        raise interrupted


def _validated(
    substitution: Substitution,
    nulls: list[Position],
    knock_ons: "_KnockOns",
    refused: _Positions,
    found: list[_Found],
) -> Document | None:
    """The document, or ``None`` with its validation errors added to ``found``."""
    try:
        # Parsed values are JSON-safe already, so validation need not copy them.
        return Document.model_validate(substitution.document, context={PARSED: True})
    except ValidationError as error:
        # No inputs: nulls are refused before validation, and no union of a document is chosen
        # by a member's value.
        for details in error.errors(include_url=False, include_input=False):
            if details["type"] == "null_not_allowed" and nulls:
                continue  # every null is already reported, at its own path
            tokens = _tokens(details["loc"])
            located, code = _classified(details, tokens)
            if _caused_by_nulls(details, located, knock_ons.lost, substitution.document):
                continue  # the object lacks a member only because a null was left out
            if not refused.covers(located):
                found.append(_Found(located, code, _error_refusal(details, tokens)))
        return None


def _null_refusal(at: str | None) -> Refusal:
    return Refusal(
        code=RefusalCode.NULL_NOT_ALLOWED,
        path=at,
        message=[text("null is not allowed in a document; omit the member instead")],
    )


def _given(refusal: Refusal) -> Callable[[str | None], Refusal]:
    return lambda at: refusal.model_copy(update={"path": at})


def _error_refusal(details: ErrorDetails, position: Position) -> Callable[[str | None], Refusal]:
    return lambda at: _refusal(details, Document, position, at)


def _position(path: str) -> Position:
    """The tokens of a JSON Pointer."""
    return tuple(token.replace("~1", "/").replace("~0", "~") for token in path.split("/")[1:])


# --- Nulls and what follows from them ------------------------------------------------------


def _nulls(value: JsonValue) -> list[Position]:
    """The position of every ``null``. A position is built only for a null found: the walk
    keeps each value's parent and key, so a long key is not copied for every value below it."""
    found: list[Position] = []
    pending: list[tuple[JsonValue, tuple[Any, str | int] | None]] = [(value, None)]
    while pending:
        current, chain = pending.pop()
        if current is None:
            tokens: list[str | int] = []
            while chain is not None:
                chain, token = chain
                tokens.append(token)
            found.append(tuple(reversed(tokens)))
        elif isinstance(current, dict):
            pending.extend((member, (chain, key)) for key, member in current.items())
        elif isinstance(current, list):
            pending.extend((item, (chain, index)) for index, item in enumerate(current))
    return found


_MAPS_AT_ROOT = ("packs", "cohorts")
"""Root members whose values are maps; ``params`` is kept whole, a null included."""


def _without_null_members(
    document: dict[str, JsonValue], dropped: dict[Position, set[str]]
) -> dict[str, JsonValue]:
    """The document without the null members of its objects, which are reported already.

    Leaving a null member out lets its siblings be checked; ``dropped`` collects, for each
    object, the members left out. Entries of maps are kept, since leaving one out could make the
    map look empty or a cohort look unknown: the maps are ``packs``, ``cohorts``, a leaf's
    ``scope`` and ``via``, and a view's ``params``. ``params`` itself is kept whole, a null
    included, so that references are marked as failed rather than unknown.
    """
    path: list[str | int] = []
    result: dict[str, JsonValue] = {}
    for key, member in document.items():
        if key == "params":
            result[key] = member
        elif member is None:
            dropped.setdefault((), set()).add(key)
        else:
            path.append(key)
            result[key] = _clean(member, key in _MAPS_AT_ROOT, path, dropped)
            path.pop()
    return result


def _clean(
    value: JsonValue, is_map: bool, path: list[str | int], dropped: dict[Position, set[str]]
) -> JsonValue:
    """``value`` without null members, at ``path`` (shared, and restored on return). A
    module-level function: a nested one would refer to itself, and so keep what it holds in a
    reference cycle after a load."""
    if isinstance(value, list):
        items: list[JsonValue] = []
        for index, item in enumerate(value):
            path.append(index)
            items.append(_clean(item, False, path, dropped))
            path.pop()
        return items
    if not isinstance(value, dict):
        return value
    leaf = "kind" in value
    result: dict[str, JsonValue] = {}
    for key, member in value.items():
        if member is None and not is_map:
            dropped.setdefault(tuple(path), set()).add(key)
            continue
        path.append(key)
        result[key] = _clean(member, _is_map(path, leaf), path, dropped)
        path.pop()
    return result


def _is_map(path: list[str | int], in_leaf: bool) -> bool:
    """Whether the member at ``path`` holds a map: a leaf's scope or via, or a view's params."""
    if in_leaf and path[-1] in ("scope", "via"):
        return True
    return len(path) == 3 and path[0] == "views" and path[2] == "params"


_CAUSES: dict[str, frozenset[str]] = {
    "not_a_clause": frozenset({"kind", "all", "any", "not", "known", "unknown"}),
    "unknown_kind": frozenset({"kind"}),
    "empty_range": frozenset({"gt", "gte", "lt", "lte"}),
}
"""Errors about an object as a whole, and the members whose absence can cause each: such an
error is not reported for an object that lost one of them to a null."""

_RULES: dict[str, tuple[tuple[str, ...], Callable[[AbstractSet[str]], bool]]] = {
    "predicate": (PREDICATE_MEMBERS, predicate_holds),
}
"""Rules on which members an object gives, by the name a conflict's context gives them: the
members each concerns, and whether a set of given members satisfies it. Other conflicts never
follow from a member left out."""


def _caused_by_nulls(
    details: ErrorDetails,
    position: Position,
    lost: Mapping[Position, set[str]],
    document: JsonValue,
) -> bool:
    """Whether an error about the object at ``position`` follows only from members it lost.

    A conflict does when giving some of the lost members would satisfy its rule: how the nulls
    are fixed decides it. Otherwise it stands whatever is done about them, and is reported.
    """
    gone = lost.get(position)
    if not gone:
        return False
    rule = _RULES.get(str((details.get("ctx") or {}).get("rule")))
    if rule is not None:
        members, holds = rule
        present = _at(document, position)
        given = {m for m in members if isinstance(present, dict) and present.get(m) is not None}
        open_ = sorted(gone.intersection(members))
        return any(
            holds(given.union(chosen))
            for size in range(len(open_) + 1)
            for chosen in combinations(open_, size)
        )
    return bool(_CAUSES.get(details["type"], frozenset()) & gone)


@dataclass(frozen=True)
class _KnockOns:
    under: list[Position]
    """Positions under which nothing is reported."""
    lost: dict[Position, set[str]]
    """Objects that lost members to nulls, left out or supplied by a parameter, with them."""
    unknown: Unknown
    """What the document checks cannot know because of a null."""


def _at(document: JsonValue, position: Position) -> JsonValue:
    """The value at ``position``, or ``None`` if there is none."""
    current = document
    for token in position:
        if isinstance(current, dict) and isinstance(token, str):
            current = current.get(token)
        elif isinstance(current, list) and isinstance(token, int) and 0 <= token < len(current):
            current = current[token]
        else:
            return None
    return current


def _is_clause(document: JsonValue, position: Position) -> bool:
    """Whether ``position`` holds a clause: an item of a cohort's ``all``, or a clause inside one.

    Inside a clause, the items of ``all`` and ``any`` and the values of ``not``, ``known`` and
    ``unknown`` of a clause without ``kind`` are clauses, and so are the items of an ``exists``
    leaf's ``where``.
    """
    if len(position) < 4 or position[0] != "cohorts" or position[2] != "all":
        return False
    node = _at(document, position[:4])
    rest = position[4:]
    while rest:
        if not isinstance(node, dict):
            return False
        step = rest[0]
        if "kind" not in node and step in ("not", "known", "unknown"):
            node, rest = node.get(step), rest[1:]
        elif len(rest) > 1 and (
            ("kind" not in node and step in ("all", "any"))
            or (node.get("kind") == "exists" and step == "where")
        ):
            node, rest = _at(node, rest[:2]), rest[2:]
        else:
            return False
    return isinstance(node, dict)


def _knock_ons(
    written: dict[str, JsonValue],
    nulls: list[Position],
    dropped: dict[Position, set[str]],
    substitution: Substitution,
) -> _KnockOns:
    """Problems that follow only from others already reported.

    A null inside a parameter's value recurs wherever the value was substituted; a clause whose
    ``kind`` is a refused reference has no shape to check; an object that lost a null member
    may look like no clause or lack a member it needs; and what a null leaves unknown is not
    checked.
    """
    under: list[Position] = []
    lost: dict[Position, set[str]] = {parent: set(names) for parent, names in dropped.items()}
    by_parameter: dict[str, list[Position]] = {}
    for position, name in substitution.positions.items():
        by_parameter.setdefault(name, []).append(position)
    for null in nulls:
        if len(null) < 2 or null[0] != "params":
            continue
        name, rest = null[1], null[2:]
        for at in by_parameter.get(str(name), []):
            substituted = (*at, *rest)
            under.append(substituted)
            lost.setdefault(substituted[:-1], set()).add(str(substituted[-1]))
    under.extend(
        position[:-1]
        for position in substitution.failed
        if position and position[-1] == "kind" and _is_clause(substitution.document, position[:-1])
    )
    return _KnockOns(under, lost, _unknown(written, dropped))


def _unknown(written: dict[str, JsonValue], dropped: dict[Position, set[str]]) -> Unknown:
    """Cohorts whose datasets or ``unmapped`` a null left unknown, and views whose cohorts."""
    cohorts = written.get("cohorts")
    datasets: set[str] = set()
    unmapped: set[str] = set()
    if isinstance(cohorts, dict):
        root_null = "dataset" in dropped.get((), set())
        for name, cohort in cohorts.items():
            if not isinstance(cohort, dict):
                continue
            lost = dropped.get(("cohorts", name), set())
            own = any(cohort.get(member) is not None for member in ("dataset", "datasets"))
            if not own and (root_null or lost & {"dataset", "datasets"}):
                datasets.add(name)
            if "unmapped" in lost:
                unmapped.add(name)
    views = written.get("views")
    view_cohorts: set[int] = set()
    if isinstance(views, list):
        view_cohorts = {
            index
            for index in range(len(views))
            if "cohorts" in dropped.get(("views", index), set())
        }
    return Unknown(frozenset(datasets), frozenset(unmapped), frozenset(view_cohorts))


def as_written(
    position: Position, positions: Mapping[Position, str]
) -> tuple[Position, str | None]:
    """The position in the document as written, and the parameter that filled it, if any.

    A position inside a substituted value becomes the position of the ``"$name"`` string.
    """
    index = _Positions()
    for filled, name in positions.items():
        index.add(filled, name)
    return _as_written(position, index)


def _as_written(position: Position, positions: _Positions) -> tuple[Position, str | None]:
    above = positions.above(position)
    if above is None:
        return position, None
    depth, name = above
    return position[:depth], str(name)


class _SortKeys:
    """Keys that order positions as their JSON Pointers do, without writing the pointers.

    A pointer is ``/t1/t2/…/tn`` with each token escaped. Keyed as ``(t1/, t2/, …, tn)`` —
    every token but the last followed by its separator — tuples compare as the pointers do: a
    key with a separator is never a proper prefix of another key at the same place, since
    escaped tokens hold no ``/``. Keys are made once per token, so they share their strings.
    """

    def __init__(self) -> None:
        self._inner: dict[str | int, str] = {}
        self._last: dict[str | int, str] = {}

    def of(self, position: Position) -> tuple[str, ...]:
        """The key of a position. It runs once per refusal found, so it is one comprehension."""
        if not position:
            return ()
        inner = self._inner
        # Inner keys end with "/", so ``or`` falls back only for a token not seen yet; the last
        # key of the empty token is empty, and is recomputed each time.
        keys = [inner.get(token) or self._inner_key(token) for token in position[:-1]]
        keys.append(self._last.get(position[-1]) or self._last_key(position[-1]))
        return tuple(keys)

    def _inner_key(self, token: str | int) -> str:
        key = self._inner[token] = escape_token(token) + "/"
        return key

    def _last_key(self, token: str | int) -> str:
        # The empty token's key is empty; it is recomputed each time, which costs nothing.
        key = self._last[token] = escape_token(token)
        return key


def _finish(found: list[_Found], positions: _Positions) -> list[Refusal]:
    """The refusals returned: one per (path, code), sorted, and at most ``MAX_REFUSALS``.

    A refusal without a path sorts with the root's, as ``sort_refusals`` has it, but is kept
    apart from one at the root.
    """
    keys = _SortKeys()
    kept: dict[tuple[tuple[str, ...], str, bool], tuple[_Found, Position | None, str | None]] = {}
    filled = positions.filled()
    for item in found:
        if item.position is None:
            written_at, parameter, key = None, None, ()
        else:
            written_at, parameter = (
                _as_written(item.position, positions) if filled else (item.position, None)
            )
            key = keys.of(written_at)
        kept.setdefault(
            (key, str(item.code), written_at is not None), (item, written_at, parameter)
        )
    order = heapq.nsmallest(MAX_REFUSALS, kept) if len(kept) > MAX_REFUSALS else sorted(kept)
    refusals: list[Refusal] = []
    for key in order:
        item, written_at, parameter = kept[key]
        refusal = item.build(None if written_at is None else pointer(written_at))
        if parameter is not None:
            message = [
                *refusal.message,
                text(" (in the value of parameter "),
                data(parameter),
                text(")"),
            ]
            refusal = refusal.model_copy(update={"message": message})
        refusals.append(refusal)
    if len(kept) > MAX_REFUSALS:
        refusals.append(
            Refusal(
                code=RefusalCode.LIMIT_EXCEEDED,
                path=None,
                message=[text(f"{len(kept) - MAX_REFUSALS} more refusals were left out")],
                limit=Limit(name=REFUSALS, max=MAX_REFUSALS),
            )
        )
    return refusals


# --- Pydantic errors to refusals -------------------------------------------------------------

_CODES: dict[str, RefusalCode] = {
    "missing": RefusalCode.MISSING_MEMBER,
    "required_member": RefusalCode.MISSING_MEMBER,
    "curation_missing": RefusalCode.MISSING_MEMBER,
    "extra_forbidden": RefusalCode.UNKNOWN_MEMBER,
    "unknown_kind": RefusalCode.UNKNOWN_KIND,
    "cross_dataset_only": RefusalCode.CROSS_DATASET_ONLY,
    "conflicting_members": RefusalCode.CONFLICTING_MEMBERS,
    "duplicate_entry": RefusalCode.DUPLICATE_ENTRY,
    "integer_out_of_range": RefusalCode.INTEGER_OUT_OF_RANGE,
    "non_finite_number": RefusalCode.NON_FINITE_NUMBER,
    "null_not_allowed": RefusalCode.NULL_NOT_ALLOWED,
    "too_long": RefusalCode.LIMIT_EXCEEDED,
    "string_too_long": RefusalCode.LIMIT_EXCEEDED,
    "recursion_loop": RefusalCode.LIMIT_EXCEEDED,
}


_NULL_MESSAGE = "null is not allowed here: give a value, or omit the member if it is optional"


class _Absent:
    """No input, or no member in it."""


_ABSENT = _Absent()


def _code(error_type: str) -> RefusalCode:
    if error_type in _CODES:
        return _CODES[error_type]
    if error_type.endswith("_type") or error_type in ("not_a_clause", "model_attributes_type"):
        return RefusalCode.WRONG_TYPE
    return RefusalCode.INVALID_VALUE


def _tag_value(details: ErrorDetails, member: str) -> object:
    given = details.get("input", _ABSENT)
    if isinstance(given, dict):
        return cast(dict[str, object], given).get(member, _ABSENT)
    return _ABSENT


def _classified(details: ErrorDetails, tokens: Position) -> tuple[Position, RefusalCode]:
    """An error's path and code, found without building its refusal.

    An error whose input is ``null`` is a null refused, unless the member is unknown. Every union
    is chosen by a function, which returns one of its tags, a member that refuses the value when
    no other fits, or ``None`` with the union's own ``custom_error_type``; so Pydantic's own tag
    errors never arise.
    """
    error_type = details["type"]
    if error_type == "unknown_kind":
        # A clause's kind that is null or not a string is refused at the kind. Only descriptors'
        # errors carry their input; a document's nulls are refused before validation.
        kind = _tag_value(details, "kind")
        if kind is None:
            return (*tokens, "kind"), RefusalCode.NULL_NOT_ALLOWED
        if not isinstance(kind, str | _Absent):
            return (*tokens, "kind"), RefusalCode.WRONG_TYPE
    if details.get("input", _ABSENT) is None and error_type not in ("missing", "extra_forbidden"):
        return tokens, RefusalCode.NULL_NOT_ALLOWED
    return tokens, _code(error_type)


@dataclass(frozen=True)
class Resolved:
    """Where an error location leads: the JSON path it names, and the annotation there."""

    tokens: list[str | int]
    positions: list[int]
    """The index in the location of each token."""
    annotation: object
    metadata: list[object]
    """The ``Annotated`` metadata at the position, such as a ``LimitName``."""


def _unwrap(annotation: object) -> tuple[object, list[object]]:
    """Remove ``Annotated`` layers and ``| None``, keeping the metadata."""
    metadata: list[object] = []
    while True:
        origin = get_origin(annotation)
        if origin is Annotated:
            args = get_args(annotation)
            annotation = cast(object, args[0])
            metadata.extend(cast(tuple[object, ...], args[1:]))
        elif origin in (Union, types.UnionType):
            members: list[object] = [arg for arg in get_args(annotation) if arg is not type(None)]
            if len(members) != 1:
                return annotation, metadata
            annotation = members[0]
        else:
            return annotation, metadata


def _discriminated(metadata: list[object]) -> bool:
    """Whether the metadata makes a union discriminated (always by a function, with tags)."""
    return any(isinstance(extra, Discriminator) for extra in metadata)


def _clause_union(metadata: list[object]) -> bool:
    """Whether the metadata makes a union the document's clause union, whose refusals list the
    leaf kinds wherever a clause is validated, a view's parameters included (D317)."""
    return any(
        isinstance(extra, Discriminator) and extra.discriminator is clause_tag for extra in metadata
    )


_TAGS: dict[int, tuple[object, dict[str, object]]] = {}
"""Tags by union, computed once: the unions are those of the models, which live as long."""


def _tags(union: object) -> dict[str, object]:
    """The members of a discriminated union, by their ``Tag``."""
    cached = _TAGS.get(id(union))
    if cached is not None and cached[0] is union:
        return cached[1]
    tags: dict[str, object] = {}
    for member in get_args(union):
        if get_origin(member) is Annotated:
            for extra in get_args(member)[1:]:
                if isinstance(extra, Tag):
                    tags[extra.tag] = member
    _TAGS[id(union)] = (union, tags)
    return tags


def resolve(loc: tuple[int | str, ...], root: object) -> Resolved:
    """Follow a Pydantic error location through the types from ``root``, a model or annotation.

    Union tags and Pydantic's labels are left out of the path by their position, so a member
    name that happens to look like a tag is kept. Where the types cannot be followed, the rest
    of the location is kept as it is, without labels.
    """
    positions: list[int] = []
    current: object = root
    carried: list[object] = []
    index = 0
    while index < len(loc):
        element = loc[index]
        current, metadata = _unwrap(current)
        metadata = carried + metadata
        carried = []
        if _discriminated(metadata):
            tags = _tags(current)
            if isinstance(element, str) and element in tags:
                current = tags[element]
                index += 1
                continue
        if isinstance(current, type) and issubclass(current, BaseModel):
            fields = {info.alias or name: info for name, info in current.model_fields.items()}
            positions.append(index)
            if not isinstance(element, str) or element not in fields:
                current = None
            else:
                current = fields[element].annotation
                carried = list(fields[element].metadata)
        elif get_origin(current) is list and isinstance(element, int):
            positions.append(index)
            current = get_args(current)[0]
        elif get_origin(current) is dict and isinstance(element, str):
            positions.append(index)
            key_type, value_type = get_args(current)
            if index + 1 < len(loc) and loc[index + 1] == _KEY_LABEL:
                current = key_type
                index += 1
            else:
                current = value_type
        else:
            positions.extend(at for at in range(index, len(loc)) if loc[at] != _KEY_LABEL)
            return Resolved([loc[at] for at in positions], positions, None, [])
        index += 1
    current, metadata = _unwrap(current)
    return Resolved([loc[at] for at in positions], positions, current, carried + metadata)


_OTHER = "\x00"
"""Stands for any string in a location's shape that is not a member name, tag or label."""


def _root(name: str) -> object:
    return Descriptor if name == "descriptor" else Document


@lru_cache(maxsize=2)
def _structural_names(root: str) -> frozenset[str]:
    """The member names, aliases and union tags of a root's models, and Pydantic's label.

    Tags are the ``Tag`` values of the unions' members.
    """
    names = {_KEY_LABEL}
    seen: set[int] = set()
    pending: list[object] = [_root(root)]
    while pending:
        annotation = pending.pop()
        if id(annotation) in seen:
            continue
        seen.add(id(annotation))
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            for name, info in annotation.model_fields.items():
                names.update(key for key in (name, info.alias) if key)
                pending.append(info.annotation)
                pending.extend(info.metadata)
        elif isinstance(annotation, Tag):
            names.add(annotation.tag)
        else:
            pending.extend(get_args(annotation))
    return frozenset(names)


@lru_cache(maxsize=4096)
def _kept(root: str, shape: tuple[str | None, ...]) -> tuple[int, ...]:
    """Which elements of an error location are path tokens, for a document or a descriptor.

    The answer depends only on the location's structure, so it is cached by its shape: list
    indices become ``None`` and strings that are not member names, tags or labels (dict keys,
    unknown members) become one placeholder. The cache therefore holds no input text, and
    many similar errors cost little.
    """
    loc = tuple(0 if element is None else element for element in shape)
    return tuple(resolve(loc, _root(root)).positions)


def _tokens(loc: tuple[int | str, ...], root: str = "document") -> Position:
    """The path tokens of an error location. It runs once per error, so it avoids generators."""
    names = _structural_names(root)
    shape = tuple(
        [
            None if element.__class__ is int else element if element in names else _OTHER
            for element in loc
        ]
    )
    return tuple([loc[index] for index in _kept(root, shape)])


def _reserved(model: object) -> Mapping[str, str]:
    """A descriptor model's reserved members, with why each is refused."""
    if isinstance(model, type) and issubclass(model, DescModel):
        return model.reserved()
    return {}


def _members(model: object) -> list[str]:
    model, _ = _unwrap(model)
    if isinstance(model, type) and issubclass(model, BaseModel):
        return sorted(info.alias or name for name, info in model.model_fields.items())
    return []


def refusal_from_error(details: ErrorDetails, root: object) -> Refusal:
    """The refusal for one Pydantic error in a value validated as ``root``."""
    tokens = tuple(resolve(details["loc"], root).tokens)
    return _refusal(details, root, tokens, pointer(_classified(details, tokens)[0]))


def _refusal(details: ErrorDetails, root: object, tokens: Position, at: str | None) -> Refusal:
    """The refusal for an error whose location names ``tokens``, reported at ``at``.

    Messages are the server's own text; a value from the input appears only as a data token.
    """
    loc = details["loc"]
    resolved = resolve(loc, root)
    path, code = _classified(details, tokens)
    error_type = details["type"]
    message: list[Segment] = [text(details["msg"])]
    alternatives: list[Segment] = []
    limit: Limit | None = None
    ctx: dict[str, Any] = dict(details.get("ctx") or {})
    if code is RefusalCode.NULL_NOT_ALLOWED:
        message = [text(_NULL_MESSAGE)]
    elif code is RefusalCode.UNKNOWN_MEMBER and path:
        parent: object = resolve(loc[:-1], root).annotation
        reason = _reserved(parent).get(str(path[-1]))
        if reason is not None:
            message = [text(f"{path[-1]} {reason}")]
        else:
            message = [text("Unknown member "), data(str(path[-1]))]
        alternatives = [text(member) for member in _members(parent)]
    elif error_type == "missing" and path:
        message = [text("Missing member "), text(str(path[-1]))]
    elif "alternatives" in ctx:
        alternatives = [text(str(choice)) for choice in ctx["alternatives"]]
    elif error_type == "unknown_kind" and code is RefusalCode.WRONG_TYPE:
        message = [text("kind is a string")]
    elif code is RefusalCode.UNKNOWN_KIND and root is Descriptor:
        # The only clauses in descriptors are parent scopes, which are core clauses.
        kind = _tag_value(details, "kind")
        message = [text("Unknown leaf kind")]
        if isinstance(kind, str):
            message = [text("Unknown leaf kind "), data(kind)]
        alternatives = [text(name) for name in PARENT_SCOPE_KINDS]
    elif (
        code is RefusalCode.UNKNOWN_KIND
        and root is not Document
        and _discriminated(resolved.metadata)
        and not _clause_union(resolved.metadata)
    ):
        # A request's union (an edit's op, an import's source) lists its own members.
        alternatives = [text(tag) for tag in _tags(resolved.annotation)]
    elif code is RefusalCode.UNKNOWN_KIND:
        alternatives = [text(kind) for kind in CORE_KINDS_TEXT.replace(" or", ",").split(", ")]
        alternatives.append(text("<pack id>.<name> for a pack leaf"))
    elif error_type == "literal_error":
        if get_origin(resolved.annotation) is Literal:
            alternatives = [text(json.dumps(value)) for value in get_args(resolved.annotation)]
    elif error_type == "recursion_loop":
        limit = Limit(name=NESTING_DEPTH, max=MAX_DEPTH)
    elif code is RefusalCode.LIMIT_EXCEEDED and "max_length" in ctx:
        named = [extra.name for extra in resolved.metadata if isinstance(extra, LimitName)]
        fallback = CONSTANT_CHARACTERS if error_type == "string_too_long" else LIST_MEMBERS
        name = str(ctx.get("limit") or (named[0] if named else fallback))
        limit = Limit(name=name, max=int(ctx["max_length"]))
    if "shown" in ctx:
        shown = ctx["shown"]
        message.append(data(shown if isinstance(shown, str) else json.dumps(shown)))
    return Refusal(
        code=code,
        path=at,
        message=message,
        alternatives=alternatives,
        limit=limit,
    )


# --- Requests --------------------------------------------------------------------------------


@dataclass(frozen=True)
class RequestResult[M: BaseModel]:
    """A validated request, or the refusals that stop it, with paths into the body as written."""

    value: M | None
    refusals: list[Refusal]


def load_request[M: BaseModel](source: bytes, model: type[M]) -> RequestResult[M]:
    """Parse and validate a request body, an operator's or a tool's (SPEC §11.2, D260): the rules
    and limits of documents first (§7.1: UTF-8, no duplicate keys, no non-finite numbers or lone
    surrogates, nesting, values and pointers bounded), then ``model``. Refusals are returned as
    ``finish_refusals`` orders them, and quote nothing of the body: errors are read without their
    inputs, and their messages are the server's own text; a member name of a token's or a
    handle's shape is blanked in their paths and messages (``blank_secrets``, D265)."""
    found = _loaded_request(source, model)
    if found.value is not None:
        return found
    return RequestResult(None, [blank_secrets(refusal) for refusal in found.refusals])


def _loaded_request[M: BaseModel](source: bytes, model: type[M]) -> RequestResult[M]:
    try:
        value = parse_json(source)
    except JsonError as problem:
        limit = Limit(name=problem.limit[0], max=problem.limit[1]) if problem.limit else None
        refusal = Refusal(
            code=RefusalCode(problem.code),
            path=problem.pointer,
            message=[text(problem.message)],
            limit=limit,
        )
        return RequestResult(None, [refusal])
    if not isinstance(value, dict):
        refusal = Refusal(
            code=RefusalCode.WRONG_TYPE, path="", message=[text("A request body is an object")]
        )
        return RequestResult(None, [refusal])
    return _paused(_validated_request, value, model)


def _validated_request[M: BaseModel](value: JsonValue, model: type[M]) -> RequestResult[M]:
    try:
        return RequestResult(model.model_validate(value), [])
    except ValidationError as error:
        found = [
            refusal_from_error(details, model)
            for details in error.errors(include_url=False, include_input=False)
        ]
        return RequestResult(None, finish_refusals(found))


# --- Descriptors -----------------------------------------------------------------------------

_DESCRIPTOR: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)


@dataclass(frozen=True)
class DescriptorResult:
    """A validated descriptor, or the refusals that stop it, with paths into the descriptor."""

    descriptor: Descriptor | None
    refusals: list[Refusal]


def load_descriptor(source: str | bytes) -> DescriptorResult:
    """Parse and validate one descriptor, such as one an agent proposes (SPEC §5, §11.1)."""
    size = len(source.encode("utf-8", "surrogatepass") if isinstance(source, str) else source)
    if size > MAX_DOCUMENT_BYTES:
        return DescriptorResult(
            None,
            [
                Refusal(
                    code=RefusalCode.LIMIT_EXCEEDED,
                    path=None,
                    message=[text("The descriptor is too large")],
                    limit=Limit(name=DESCRIPTOR_BYTES, max=MAX_DOCUMENT_BYTES),
                )
            ],
        )
    try:
        value = parse_json(source)
    except JsonError as problem:
        limit = Limit(name=problem.limit[0], max=problem.limit[1]) if problem.limit else None
        return DescriptorResult(
            None,
            [
                Refusal(
                    code=RefusalCode(problem.code),
                    path=problem.pointer,
                    message=[text(problem.message)],
                    limit=limit,
                )
            ],
        )
    return _paused(_validated_descriptor, value)


def _validated_descriptor(value: JsonValue) -> DescriptorResult:
    try:
        descriptor = _DESCRIPTOR.validate_python(value)
    except ValidationError as error:
        banned = _banned_in_scope(value)
        found = [_Found((*at, "kind"), RefusalCode.INVALID_VALUE, _banned_refusal) for at in banned]
        inside = _Positions()
        for at in banned:
            inside.add(at)
        for details in error.errors(include_url=False):
            tokens = _tokens(details["loc"], "descriptor")
            path, code = _classified(details, tokens)
            if not inside.covers(path):
                found.append(_Found(path, code, _descriptor_refusal(details, tokens)))
        return DescriptorResult(None, _finish(found, _Positions()))
    return DescriptorResult(descriptor, [])


def _banned_in_scope(value: JsonValue) -> list[Position]:
    """The clauses of a coverage's parent scope, as written, of a kind no parent scope holds:
    pack, ids and cohort leaves (§5.6).

    Each is refused at its kind, and what else is wrong inside it is left out, since it must go
    whatever it holds.
    """
    if not isinstance(value, dict) or value.get("kind") != "coverage":
        return []
    fields = value.get("fields")
    if not isinstance(fields, dict) or "parent_scope" not in fields:
        return []
    banned: list[Position] = []
    pending: list[tuple[JsonValue, Position]] = [
        (fields["parent_scope"], ("fields", "parent_scope"))
    ]
    while pending:
        clause, at = pending.pop()
        tag = clause_tag(clause)
        if tag in ("leaf:pack", "leaf:ids", "leaf:cohort"):
            banned.append(at)
        elif isinstance(clause, dict):
            # Only the shapes the models read: a list in where, all and any; one clause else.
            member = "where" if tag == "leaf:exists" else tag.removeprefix("clause:")
            inner = clause.get(member)
            # The model reads no item of a list too long for it, only refuses its length.
            if isinstance(inner, list) and member in ("where", "all", "any"):
                if len(inner) <= MAX_CLAUSES:
                    pending.extend((item, (*at, member, index)) for index, item in enumerate(inner))
            elif isinstance(inner, dict) and member in ("not", "known", "unknown"):
                pending.append((inner, (*at, member)))
    return banned


def _banned_refusal(at: str | None) -> Refusal:
    """The refusal the model gives a pack, ids or cohort leaf in a parent scope, at ``at``."""
    error = parent_scope_kind()
    return Refusal(
        code=RefusalCode.INVALID_VALUE,
        path=at,
        message=[text(error.message())],
        alternatives=[text(kind) for kind in PARENT_SCOPE_KINDS],
    )


def _descriptor_refusal(details: ErrorDetails, tokens: Position) -> Callable[[str | None], Refusal]:
    return lambda at: _refusal(details, Descriptor, tokens, at)

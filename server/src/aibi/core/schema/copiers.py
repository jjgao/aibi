"""The copiers: what a pack's hook gave, copied as the core keeps it (SPEC §10.1, D388).

Every call of a pack's code is ``hook.call(run)`` (``schema.guards``), and every ``run`` is one
copier's call, ``lambda h: COPIER(h.METHOD, ...)`` or ``lambda h: COPIER(h, ...)``: the copier
calls the hook itself, inside the guard, and returns a copy of the core's of what it gave, or a
marker of the core's (``Tripped``, ``NotJson``, ``Late``, ``NoExpansion``); anything it does not
take is the hook's failure. Each copier obeys one **construction rule**:

- **built-ins** are built anew, from items read by the built-in types' own methods
  (``list.__iter__``, ``dict.items``), each scalar made an exact built-in (``str.__str__``,
  ``int.__index__``, ``float.__float__``, ``is True``); a JSON value of an analysis or a
  translation is read by ``pack_api._plain``, whose reads of a mapping (its ``items``) run inside
  the guard and whose copy is exact;
- **core models** are made only by dumping a value through its class's ``__pydantic_serializer__``
  and reading the JSON text back (never Python mode, which keeps an object held in an ``Any``
  field), or by calling a core constructor with values the copier built itself;
- **never a pack's object** in a core constructor, ``model_validate`` or ``model_copy``;
- **enum members** by identity, from the table of ``aibi.core.schema``'s enums (``_ENUMS``): an
  object of an enum's type that is no member (``str.__new__(CaveatCode, …)``) is the hook's
  failure, and ``Kind(x)`` is never called on what a pack gave;
- **no state**: nothing is cached, and no module-level binding is written after import.

Before any serializer runs on a pack-made model, the **count walk** (``_counted``) reads it by the
``_fields`` rule (the exact class; a plain-``dict`` instance dictionary of exact-``str`` keys; for a
pydantic model a ``__pydantic_fields_set__`` that is an exact ``set`` of exact ``str``, and no
extra or private members), every container by its built-in type's own method and every enum
member by identity, counting every value along every path, at most ``MAX_DEPTH`` deep, against
the copier's own fixed allowance: so a model that shares one part many times (a DAG), or nests
past the depth, is refused before a serializer spends its time on it, and the serializers then
read only the core's code. A trip of a fixed allowance is the hook's failure with no ``Limit``,
since those bounds are the core's (D388).

Every refusal a copier makes of its own (what it does not take, a fixed allowance's trip, a
read-back that fails) is raised as ``guards.Unfit`` or ``guards._Foreign``, the core's family
that ``pack_failed`` logs, by identity, as what the core does not take.
"""

import json
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import cast

from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError
from pydantic_core import PydanticSerializationError

from aibi.core.schema.analyses import Direction
from aibi.core.schema.caveats import CaveatCode, Severity
from aibi.core.schema.cohorts import TranslationNote
from aibi.core.schema.curation import ProposalInput
from aibi.core.schema.document import (
    AllClause,
    AnyClause,
    Clause,
    ClauseModel,
    CohortLeaf,
    CoveredLeaf,
    ExistsLeaf,
    IdsLeaf,
    KnownClause,
    NotClause,
    PackLeaf,
    Range,
    SomeAtLeast,
    Step,
    UnknownClause,
    ValueLeaf,
    walk,
)
from aibi.core.schema.guards import (
    Allowances,
    JsonTooLarge,
    Late,
    NotJson,
    Tripped,
    Unfit,
    _Allowance,  # pyright: ignore[reportPrivateUsage]
    _Foreign,  # pyright: ignore[reportPrivateUsage]
    _NotJsonError,  # pyright: ignore[reportPrivateUsage]
    contain,
)
from aibi.core.schema.ids import IDENTIFIER_RE, JSON_POINTER_RE, is_pack_code
from aibi.core.schema.jsonio import JsonError, is_text, json_value
from aibi.core.schema.limits import (
    JSON_VALUES,
    MAX_DEPTH,
    MAX_ENTRIES,
    MAX_POINTER,
    MAX_QUEUE_ITEMS,
    MAX_REFUSALS,
    MAX_STRING,
    MAX_SUMMARY_SEGMENTS,
    MAX_TEXT,
    MAX_VALUES,
    STRING_CHARACTERS,
    TEXT_CHARACTERS,
)
from aibi.core.schema.numbers import EffectMeasure, NotEstimableReason
from aibi.core.schema.output import DataSegment, Segment, TextSegment
from aibi.core.schema.pack_api import (
    Proposal,
    Refused,
    _plain,  # pyright: ignore[reportPrivateUsage]
)
from aibi.core.schema.pack_api import TranslationNote as PackNote
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode
from aibi.core.schema.semantics import ExclusionReason, Flag, ObservationState, Reason

_ENUMS: tuple[type[Enum], ...] = (
    Direction,
    Severity,
    CaveatCode,
    NotEstimableReason,
    EffectMeasure,
    RefusalCode,
    ObservationState,
    Reason,
    Flag,
    ExclusionReason,
)
"""Every enum of ``aibi.core.schema`` (a test lists them from its modules): what a copied model
may hold of an enum is one of their members, found by identity."""

_MEMBERS: Mapping[int, Enum] = {id(member): member for kind in _ENUMS for member in kind}
"""Each member of ``_ENUMS``, by its ``id``, compared by ``is``: built at import, never written."""

_NAMES = (JSON_VALUES, TEXT_CHARACTERS, STRING_CHARACTERS)
"""The names of a fixed allowance's limits, which a trip never quotes (module docstring)."""

# The built-in types' own methods, which read a container without calling any of its members'.
_DICT_ITEMS = cast(
    Callable[[dict[object, object]], Iterable[tuple[object, object]]],
    dict.items,  # pyright: ignore[reportUnknownMemberType]
)
_DICT_VALUES = cast(
    Callable[[dict[object, object]], Iterable[object]],
    dict.values,  # pyright: ignore[reportUnknownMemberType]
)
_SET_ITEMS = cast(
    Callable[[set[object]], Iterable[object]],
    set.__iter__,  # pyright: ignore[reportUnknownMemberType]
)
_LIST_LENGTH = cast(Callable[[list[object]], int], list.__len__)  # pyright: ignore[reportUnknownMemberType]
_TUPLE_LENGTH = cast(
    Callable[[tuple[object, ...]], int],
    tuple.__len__,  # pyright: ignore[reportUnknownMemberType]
)
_LIST_SLICE = cast(
    Callable[[list[object], slice], list[object]],
    list.__getitem__,  # pyright: ignore[reportUnknownMemberType]
)
_TUPLE_SLICE = cast(
    Callable[[tuple[object, ...], slice], tuple[object, ...]],
    tuple.__getitem__,  # pyright: ignore[reportUnknownMemberType]
)


def _length(given: object) -> int:
    """The length of an exact ``list`` or ``tuple``, by the type's own method."""
    if type(given) is list:
        return _LIST_LENGTH(cast(list[object], given))
    if type(given) is tuple:
        return _TUPLE_LENGTH(cast(tuple[object, ...], given))
    raise Unfit("not a list")


def _items(given: object, most: int | None = None) -> list[object]:
    """The items of an exact ``list`` or ``tuple``, at most ``most`` of them, read by the
    type's own methods."""
    end = _length(given) if most is None else min(most, _length(given))
    if type(given) is list:
        return list(_LIST_SLICE(cast(list[object], given), slice(0, end)))
    return list(_TUPLE_SLICE(cast(tuple[object, ...], given), slice(0, end)))


def _text(given: object) -> str:
    """An exact ``str`` of what is a ``str`` or a subclass of one, made by ``str.__str__``,
    which runs none of a subclass's methods; the type is read from its method resolution order,
    never by ``isinstance``, which reads the object's ``__class__``."""
    if not type.__subclasscheck__(str, type(given)):
        raise Unfit("not text")
    return str.__str__(cast(str, given))


def _fields(given: object, model: bool) -> dict[str, object] | None:
    """The instance dictionary of an object of an exact core type that a pack built, when it
    meets the ``_fields`` precondition (D385): a plain ``dict`` of exact-``str`` keys, and for a
    pydantic model a ``__pydantic_fields_set__`` that is an exact ``set`` of exact ``str`` and no
    extra or private members, so that a serializer's reads of them run only built-in methods
    (``Output._omit_absent``'s, ``DocModel._dump_given``'s); ``None`` otherwise."""
    held = cast(object, vars(given))
    if type(held) is not dict:
        return None
    members = cast(dict[object, object], held)
    if not all(type(key) is str for key, _ in _DICT_ITEMS(members)):
        return None
    if model:
        made = cast(BaseModel, given)
        given_set = cast(object, made.__pydantic_fields_set__)
        if type(given_set) is not set:
            return None
        if not all(type(name) is str for name in _SET_ITEMS(cast(set[object], given_set))):
            return None
        if made.__pydantic_extra__ is not None or made.__pydantic_private__ is not None:
            return None
    return cast(dict[str, object], members)


def _named(given: object, *names: str) -> tuple[object, ...]:
    """The named fields of a dataclass or an exception of the core's that a pack built, read by
    the ``_fields`` rule; ``Unfit`` otherwise."""
    members = _fields(given, model=False)
    if members is None or not all(name in members for name in names):
        raise Unfit("an object whose fields are not its own")
    return tuple(members[name] for name in names)


def _counted(
    value: object, allowance: _Allowance, models: tuple[type, ...], depth: int = 0
) -> None:
    """The count walk (module docstring): every value of ``value`` along every path counted
    against ``allowance``, each of an exact type it takes (``models`` among them), no deeper than
    ``MAX_DEPTH``, every enum member a real one by identity; it reads no code of the pack's.
    ``_Foreign`` for a value of another type, ``Unfit`` for an enum's object that is no member or
    a model whose fields are not its own, and ``JsonTooLarge`` when ``allowance`` is passed."""
    allowance.value()
    kind = type(value)
    if value is None or kind is bool or kind is int or kind is float:
        return
    if kind is str:
        allowance.string(str.__len__(cast(str, value)))
        return
    if any(kind is enum for enum in _ENUMS):
        if _MEMBERS.get(id(value)) is not value:
            raise Unfit("an enumeration's object that is no member")
        return
    if depth >= MAX_DEPTH:
        raise Unfit(f"nesting deeper than {MAX_DEPTH}")
    if kind is list or kind is tuple:
        for item in _items(value):
            _counted(item, allowance, models, depth + 1)
        return
    if kind is dict:
        for key, member in _DICT_ITEMS(cast(dict[object, object], value)):
            if type(key) is not str:
                raise _Foreign("a key that is not text")
            allowance.string(str.__len__(key))
            _counted(member, allowance, models, depth + 1)
        return
    if any(kind is model for model in models):
        members = _fields(value, model=issubclass(kind, BaseModel))
        if members is None:
            raise Unfit("a model whose fields are not its own")
        for member in _DICT_VALUES(cast(dict[object, object], members)):
            _counted(member, allowance, models, depth + 1)
        return
    raise _Foreign("a value of another type")


def _count(value: object, count: _Allowance, models: tuple[type, ...]) -> None:
    """The count walk (``_counted``) under a fixed allowance of the copier's own, ``count``: its
    trip, by identity, is what the core does not take (``Unfit``), with no ``Limit``."""
    try:
        _counted(value, count, models)
    except JsonTooLarge as large:
        if large is not count.tripped:
            raise
        raise Unfit("more than the core takes") from None


def _read_back[M: BaseModel](model: type[M], counted: object) -> M:
    """A counted object of the exact class ``model``, dumped by the class's serializer and read
    back from that JSON text; a dump or a read-back that fails is what the core does not take
    (``Unfit``)."""
    try:
        return model.model_validate_json(
            model.__pydantic_serializer__.to_json(counted, warnings="error")
        )
    except (PydanticSerializationError, ValidationError):
        raise Unfit("what the core's model does not read back") from None


# --- Refusals ------------------------------------------------------------------------------------

_REFUSAL_MODELS: tuple[type, ...] = (Refusal, TextSegment, DataSegment, Limit)
"""What a refusal's count walk takes: a ``Limit`` among them, so that a refusal naming one is
read back and refused by ``_refusal``'s own check, in the core's words."""
_REFUSAL_VALUES = 8 * MAX_SUMMARY_SEGMENTS + 16
"""The values one refusal may hold as the count walk counts them: its members, and a message and
alternatives of at most ``MAX_SUMMARY_SEGMENTS`` segments each."""
_REFUSAL_CHARACTERS = MAX_POINTER + 2 * MAX_TEXT + MAX_STRING


def _bounded(segments: Sequence[Segment]) -> bool:
    """Whether a refusal's message or alternatives are within a note's bounds."""
    total = sum(len(s.text if isinstance(s, TextSegment) else s.data) for s in segments)
    return len(segments) <= MAX_SUMMARY_SEGMENTS and total <= MAX_TEXT


def _refusal(item: object, pack: str) -> Refusal:
    """One refusal a hook gave, read back as the core's (D388): exactly a ``Refusal``, counted
    first, dumped by its class's serializer and read back from JSON; in the pack's own codes,
    naming no limit and holding no counts, its path a pointer of at most ``MAX_POINTER``
    characters, and its message and alternatives within a note's bounds."""
    if type(item) is not Refusal:
        raise Unfit("not a refusal")
    count = _Allowance(_REFUSAL_VALUES, _REFUSAL_CHARACTERS, MAX_POINTER, _NAMES)
    _count(item, count, _REFUSAL_MODELS)
    copy = _read_back(Refusal, item)
    if copy.limit is not None or copy.counts is not None:
        raise Unfit("a refusal that names a limit or holds counts")
    if not is_pack_code(copy.code) or copy.code.partition(".")[0] != pack:
        raise Unfit("a refusal in a code that is not the pack's own")
    if not _bounded(copy.message) or not _bounded(copy.alternatives):
        raise Unfit("a refusal past a note's bounds")
    return copy


def _refusals(found: object, pack: str) -> tuple[Refusal, ...]:
    """A hook's refusals: an exact ``list`` or ``tuple``, of which at most ``MAX_REFUSALS`` + 1
    are read, each read back (``_refusal``); the site's ``finish_refusals`` keeps
    ``MAX_REFUSALS`` of them and says that there were more."""
    return tuple(_refusal(item, pack) for item in _items(found, MAX_REFUSALS + 1))


def pack_refusals(method: Callable[..., object], *given: object, pack: str) -> tuple[Refusal, ...]:
    """A validator's refusals: ``method(*given)``, copied (``_refusals``)."""
    return _refusals(method(*given), pack)


# --- Truth ---------------------------------------------------------------------------------------


def is_true(hook: Callable[..., object], *given: object) -> bool:
    """Whether ``hook(*given)`` is ``True`` itself, by identity, which runs nothing of what it
    gave: a predicate's or an ontology validator's answer."""
    return hook(*given) is True


# --- Segments ------------------------------------------------------------------------------------

_SEGMENT_MODELS: tuple[type, ...] = (TextSegment, DataSegment)


def _segments(found: object) -> tuple[Segment, ...]:
    """At most ``MAX_SUMMARY_SEGMENTS`` segments, holding at most ``MAX_TEXT`` characters
    together, each exactly a ``TextSegment`` or a ``DataSegment``, counted first, dumped by its
    class's serializer and read back from JSON."""
    if _length(found) > MAX_SUMMARY_SEGMENTS:
        raise Unfit("more segments than a summary holds")
    items = _items(found)
    count = _Allowance(4 * MAX_SUMMARY_SEGMENTS + 1, MAX_TEXT, MAX_TEXT, _NAMES)
    _count(items, count, _SEGMENT_MODELS)
    copied: list[Segment] = []
    for item in items:
        if type(item) is TextSegment:
            copied.append(_read_back(TextSegment, item))
        elif type(item) is DataSegment:
            copied.append(_read_back(DataSegment, item))
        else:
            raise Unfit("not a segment")
    return tuple(copied)


def segments(method: Callable[..., object], *given: object) -> tuple[Segment, ...]:
    """A leaf's summary: ``method(*given)``, copied (``_segments``)."""
    return _segments(method(*given))


# --- Expansions ----------------------------------------------------------------------------------

_CLAUSE_MODELS: tuple[type, ...] = (
    ValueLeaf,
    ExistsLeaf,
    CoveredLeaf,
    AllClause,
    AnyClause,
    NotClause,
    KnownClause,
    UnknownClause,
    Range,
    Step,
    SomeAtLeast,
)
"""What an expansion may hold: core clauses with no pack, ``ids`` or ``cohort`` leaf (D285)."""
_TOP: tuple[type, ...] = _CLAUSE_MODELS[:8]
_CLAUSES: TypeAdapter[list[Clause]] = TypeAdapter(list[Clause])
EXPANSION_RATIO = 5
"""The most values the count walk counts per JSON value an expansion writes (D388). The walk
counts a model and every member of it; its JSON writes the object and its members that are set
and not ``None``, every required member among them. So of a model with ``F`` members, ``r`` of
them required, the walk counts at most ``F - r`` values its JSON does not write, against at least
``1 + r`` that it does (the object and its required members), and every other value is counted
by both; over a whole expansion the walk counts at most ``1 + max (F - r) / (1 + r)`` times the
JSON values, the largest over ``_CLAUSE_MODELS``: ``Range``'s four optional bounds against its
one object, 5 (``ValueLeaf``'s 10 against 3 give 4.33, ``ExistsLeaf``'s 6 against 3 give 3). A
test computes it from the models' fields."""
_EXPANSION_VALUES = EXPANSION_RATIO * MAX_VALUES
"""The values the count walk takes for one expansion, so that every expansion within
``expansion_values``' exact bound on its JSON, which the site checks on the read-back, passes the
walk."""
_EXPANSION_CHARACTERS = sys.maxsize
"""The characters the count walk takes for one expansion: no bound of its own (D388). The core's
own limits bound an expansion's text only per string (every string member of a clause model, a
constant included, holds at most ``MAX_STRING`` characters, which the walk's per-string bound
repeats, and a test checks the clause models for it) and its size only by ``expansion_values``,
which counts JSON values: an expansion of ``MAX_VALUES`` values may hold about ``MAX_VALUES`` times
``MAX_STRING`` characters, far more than any bound of ``MAX_DOCUMENT_BYTES`` times a ratio, so any
total below that would refuse an expansion the exact bounds take. The walk's characters are
therefore bounded only through its values and the per-string bound; the strings it reads are the
pack's own, already made, and it allocates nothing for them."""


@dataclass(frozen=True)
class Expansion:
    """A leaf's expansion as the core keeps it: clauses read back from JSON, and that JSON."""

    clauses: tuple[ClauseModel, ...]
    written: JsonValue


@dataclass(frozen=True)
class CompilerRefused:
    """A compiler's refusals (``Refused`` itself), read back as refusals."""

    refusals: tuple[Refusal, ...]


@dataclass(frozen=True)
class NoExpansion:
    """What a compiler gave that is no expansion: not a list of core clauses valid as a
    document's, with no pack, ``ids`` or ``cohort`` leaf."""


def expansion(
    method: Callable[..., object], *given: object, pack: str
) -> Expansion | CompilerRefused | NoExpansion:
    """A leaf's expansion: ``method(*given)``, copied (D285, D388). A ``Refused`` itself (never a
    subclass) gives its refusals, read as a validator's are; anything else it raises is the
    hook's failure."""
    try:
        found = method(*given)
    except Refused as refused:
        if type(refused) is not Refused:
            raise
        (held,) = _named(refused, "refusals")
        return CompilerRefused(_refusals(held, pack))
    if type(found) is not list and type(found) is not tuple:
        return NoExpansion()
    items = _items(cast(object, found))
    if not all(any(type(item) is model for model in _TOP) for item in items):
        return NoExpansion()
    count = _Allowance(_EXPANSION_VALUES, _EXPANSION_CHARACTERS, MAX_STRING, _NAMES)
    try:
        _count(items, count, _CLAUSE_MODELS)
    except _Foreign:
        return NoExpansion()
    try:
        dumped = _CLAUSES.dump_json(cast(list[Clause], items), by_alias=True, warnings="error")
        checked = _CLAUSES.validate_json(dumped)
    except (PydanticSerializationError, ValidationError):
        return NoExpansion()
    for member, _, _ in walk(list(checked), []):
        if type(member) is PackLeaf or type(member) is IdsLeaf or type(member) is CohortLeaf:
            return NoExpansion()
    return Expansion(tuple(checked), cast(JsonValue, json.loads(dumped)))


# --- Caveat codes and facets ---------------------------------------------------------------------


def caveat_codes(
    rule: Callable[..., object], *given: object, codes: Mapping[str, Severity]
) -> tuple[str, ...]:
    """The codes a caveat rule raised: an exact ``list`` or ``tuple`` of text, each a code its
    pack declares (``codes``), copied as exact ``str`` (D287)."""
    found = tuple(_text(code) for code in _items(rule(*given)))
    if not all(code in codes for code in found):
        raise Unfit("a code the pack does not declare")
    return found


def facet_values(facet: Callable[..., object], *given: object) -> dict[str, tuple[str, ...]]:
    """A facet's values: an exact ``dict`` of at most ``MAX_ENTRIES`` names, each an identifier,
    to an exact ``list`` or ``tuple`` of at most ``MAX_ENTRIES`` values, each Unicode text of 1 to
    ``MAX_STRING`` characters, copied as exact ``str``."""
    found = facet(*given)
    if type(found) is not dict:
        raise Unfit("not a mapping")
    items = list(_DICT_ITEMS(cast(dict[object, object], found)))
    if len(items) > MAX_ENTRIES:
        raise Unfit("more facets than a pack gives")
    kept: dict[str, tuple[str, ...]] = {}
    for name, listed in items:
        text = _text(name)
        if IDENTIFIER_RE.fullmatch(text) is None or "__" in text or text in kept:
            raise Unfit("a facet's name that is not an identifier, or given twice")
        if _length(listed) > MAX_ENTRIES:
            raise Unfit("more values than a facet holds")
        copies = tuple(_text(value) for value in _items(listed))
        if not all(0 < len(value) <= MAX_STRING and is_text(value) for value in copies):
            raise Unfit("a facet's value that is not text")
        kept[text] = copies
    return kept


# --- Translations --------------------------------------------------------------------------------


def _notes(given: object) -> tuple[TranslationNote, ...]:
    """A translator's notes: at most ``MAX_REFUSALS``, each exactly a ``pack_api.TranslationNote``
    whose pointer is a JSON Pointer of at most ``MAX_POINTER`` characters and whose message is at
    most ``MAX_SUMMARY_SEGMENTS`` segments, read back (``_segments``)."""
    if _length(given) > MAX_REFUSALS:
        raise Unfit("more notes than a translation holds")
    found: list[TranslationNote] = []
    for note in _items(given):
        if type(note) is not PackNote:
            raise Unfit("not a note")
        at, message = _named(note, "pointer", "message")
        if type(at) is not str:
            raise Unfit("a note's pointer that is not text")
        pointer = at
        if len(pointer) > MAX_POINTER or JSON_POINTER_RE.fullmatch(pointer) is None:
            raise Unfit("a note's pointer that is not a JSON Pointer")
        found.append(
            TranslationNote(pointer=pointer, translated=False, message=list(_segments(message)))
        )
    return tuple(found)


def translation(
    method: Callable[..., object], *given: object, allowance: _Allowance
) -> tuple[dict[str, JsonValue], tuple[TranslationNote, ...]] | Tripped | NotJson:
    """A translation: ``method(*given)`` an exact pair of a mapping, the document, copied as plain
    JSON within ``allowance`` (``Tripped`` when that passes, ``NotJson`` when the copy finds what
    JSON text cannot carry unchanged), and its notes (``_notes``) (D303, D388)."""
    made = method(*given)
    if type(made) is not tuple or _length(cast(object, made)) != 2:
        raise Unfit("not a document and notes")
    translated, notes = _items(cast(object, made))
    try:
        copied = _plain(translated, allowance)
    except JsonTooLarge as large:
        if large is not allowance.tripped:
            raise
        return Tripped(large.name, large.most)
    except _NotJsonError as refused:
        if refused is not allowance.refused:
            raise
        return NotJson()
    if type(copied) is not dict:
        raise Unfit("a document that is not an object")
    try:
        document = json_value(copied)
    except JsonError:
        return NotJson()
    return cast(dict[str, JsonValue], document), _notes(notes)


# --- An analysis's values ------------------------------------------------------------------------


def values(
    method: Callable[..., object], *given: object, allowance: _Allowance, ends: float | None
) -> JsonValue | Tripped | NotJson | Late:
    """What an analysis's ``run`` gave (D343): ``Late`` when it returned past the call's deadline
    ``ends``, looked at before anything is copied, so that the site refuses it outside the guard;
    else copied as plain JSON within ``allowance`` (``Tripped`` when that passes, ``NotJson`` when
    the copy finds what JSON text cannot carry unchanged). A limit's error or a ``ValueError`` of
    the pack's own is its failure."""
    found = method(*given)
    if ends is not None and time.monotonic() >= ends:
        return Late()
    try:
        return _plain(found, allowance)
    except JsonTooLarge as large:
        if large is not allowance.tripped:
            raise
        return Tripped(large.name, large.most)
    except _NotJsonError as refused:
        if refused is not allowance.refused:
            raise
        return NotJson()


# --- Proposals -----------------------------------------------------------------------------------


UNSHOWN = TextSegment(text="(a name the core does not show)")
"""What a report shows for a name a proposal gave that is not Unicode text of at most
``MAX_STRING`` characters, or for an item that is no proposal: the core's fixed words, as a
segment of the core's (``TextSegment``) where a name the pack gave is text, so that a pack that
writes the same words is shown as its own data, never as the core's (D388)."""

ShownName = str | TextSegment
"""A name a proposal gave as a report shows it: the pack's exact text, or ``UNSHOWN``."""


@dataclass(frozen=True)
class ProposalItem:
    """One item a proposer gave: what it names, as a report shows it (``_shown``), and either
    its proposal, as the core's ``ProposalInput``, or why it is skipped (``code``)."""

    descriptor: ShownName
    pointer: ShownName
    proposal: ProposalInput | None
    code: str | None


_INVALID = RefusalCode.INVALID_VALUE.value
_FAILED = RefusalCode.PACK_FAILED.value


def _name(given: object) -> str | None:
    """What a proposal names, copied as exact text (a ``str`` subclass's by ``str.__str__``),
    or ``None`` when it is not text."""
    if not type.__subclasscheck__(str, type(given)):
        return None
    return str.__str__(cast(str, given))


def _shown(name: str | None) -> ShownName:
    """A name a proposal gave as a report shows it: its exact copy when that is Unicode text of
    at most ``MAX_STRING`` characters, else ``UNSHOWN``."""
    if name is None or str.__len__(name) > MAX_STRING or not is_text(name):
        return UNSHOWN
    return name


def _value(given: object, own: _Allowance, allowances: Allowances) -> JsonValue | Tripped | NotJson:
    """A proposal's value, copied as plain JSON within its own allowance, ``own``, which
    ``allowances`` issued: only ``own``'s trip, by the set's identity check
    (``Allowances.tripped``), is a limit, and only ``own``'s finding, by identity, is
    ``NotJson``."""
    try:
        return _plain(given, own)
    except JsonTooLarge as large:
        tripped = allowances.tripped(own, large)
        if tripped is None:
            raise
        return tripped
    except _NotJsonError as refused:
        if refused is not own.refused:
            raise
        return NotJson()


def proposals(
    proposer: Callable[..., object], *given: object, pack: str, allowances: Allowances
) -> tuple[ProposalItem, ...]:
    """A proposer's proposals (D249, D388): ``proposer(*given)`` an exact ``list`` or ``tuple`` of
    at most ``MAX_QUEUE_ITEMS``, more being the hook's failure before any is read, each item read
    on its own. An item that is not exactly a ``Proposal`` with fields of its own,
    or whose value passes its allowance (one ``allowances`` issues it) or is not JSON, or that
    is no proposal, is skipped as ``INVALID_VALUE``; one whose reading raises is skipped as
    ``PACK_FAILED`` under ``contain``, the passed types passing; the others stand. Each item's
    names are given as a report shows them (``_shown``); ``remove`` proposes a removal only when
    it is ``True`` itself."""
    made = proposer(*given)
    if _length(made) > MAX_QUEUE_ITEMS:
        raise Unfit("more proposals than the curation queue holds")
    found: list[ProposalItem] = []
    for item in _items(made):
        members = _fields(item, model=False) if type(item) is Proposal else None
        if members is None or not all(
            name in members for name in ("descriptor", "pointer", "value", "remove", "evidence")
        ):
            found.append(ProposalItem(UNSHOWN, UNSHOWN, None, _INVALID))
            continue
        descriptor, pointer = _name(members["descriptor"]), _name(members["pointer"])
        shown = (_shown(descriptor), _shown(pointer))
        evidence, raw = members["evidence"], members["value"]
        own = allowances.issue()
        ok, value = contain(pack, "proposer", lambda raw=raw, own=own: _value(raw, own, allowances))
        if not ok:
            found.append(ProposalItem(*shown, None, _FAILED))
            continue
        refused = type(value) is Tripped or type(value) is NotJson
        if refused or descriptor is None or pointer is None:
            found.append(ProposalItem(*shown, None, _INVALID))
            continue
        written: dict[str, object] = {"descriptor": descriptor, "pointer": pointer}
        if members["remove"] is True:
            written["remove"] = True
        else:
            written["value"] = value
        if evidence is not None:
            noted = _name(evidence)
            if noted is None:
                found.append(ProposalItem(*shown, None, _INVALID))
                continue
            written["evidence"] = noted
        try:
            proposal = ProposalInput.model_validate(written)
        except ValidationError:
            found.append(ProposalItem(*shown, None, _INVALID))
            continue
        found.append(ProposalItem(*shown, proposal, None))
    return tuple(found)


COPIERS = (
    pack_refusals,
    is_true,
    proposals,
    facet_values,
    caveat_codes,
    expansion,
    segments,
    translation,
    values,
)
"""The copiers: every ``run`` a hook is called with ends in one of them (D388)."""


__all__ = [
    "COPIERS",
    "UNSHOWN",
    "CompilerRefused",
    "Expansion",
    "NoExpansion",
    "ProposalItem",
    "ShownName",
    "caveat_codes",
    "expansion",
    "facet_values",
    "is_true",
    "pack_refusals",
    "proposals",
    "segments",
    "translation",
    "values",
]

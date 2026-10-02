"""The copiers (SPEC §10.1, D388): each builds what it returns from scratch, so that nothing a pack
made, or still holds, reaches core data. Each copier is driven through a ``Hook`` by a test hook
that returns, in turn, a valid value and the reviews' adversarial ones: a fake enum member (P1), a
``__pydantic_fields_set__`` of a subclass (P2), an instance dictionary of a subclass (P3), a core
model built around the pack's segments (P7, today's ``_notes``), a ``model_construct``ed refusal,
a tuple DAG, a deep chain and a subclass of a core model whose serializer records. Each is the
hook's failure, a marker of the core's, or a copy made of the core's objects alone that shares
nothing mutable with what the pack built (``provenance``) and that nothing the pack does later
changes (keep-and-mutate)."""

import importlib
import inspect
import logging
import math
import re
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from types import MappingProxyType
from typing import Any, cast

import pytest
from pydantic import BaseModel, SerializerFunctionWrapHandler, TypeAdapter, model_serializer
from tests.core import provenance

from aibi.core.schema import copiers, guards
from aibi.core.schema.caveats import Severity
from aibi.core.schema.copiers import (
    EXPANSION_RATIO,
    UNSHOWN,
    CompilerRefused,
    Expansion,
    NoExpansion,
    caveat_codes,
    expansion,
    facet_values,
    is_true,
    pack_refusals,
    proposals,
    segments,
    translation,
    values,
)
from aibi.core.schema.document import AllClause, Clause, ExistsLeaf, NotClause, ValueLeaf
from aibi.core.schema.guards import Hook, Late, NotJson, PackFailed, Tripped, Unfit
from aibi.core.schema.limits import (
    MAX_DEPTH,
    MAX_QUEUE_ITEMS,
    MAX_REFUSALS,
    MAX_STRING,
    MAX_TEXT,
    MAX_VALUES,
)
from aibi.core.schema.output import DataSegment, TextSegment, data, text
from aibi.core.schema.pack_api import Proposal, Refused
from aibi.core.schema.pack_api import TranslationNote as PackNote
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode

PACK = "library"
CODES: Mapping[str, Severity] = {"library.LATE": Severity.WARN}
NAMES = ("result_values", "result_characters", "text_characters")


class Giving:
    """A hook object whose every method gives what ``make`` builds, keeping it and its
    arguments, so that the test can walk and change what the pack holds."""

    def __init__(self, make: Callable[[], object]) -> None:
        self.make = make
        self.kept: list[object] = []
        self.args: list[object] = []

    def _give(self, *args: object) -> object:
        self.args.append(args)
        value = self.make()
        self.kept.append(value)
        return value

    compile = summary = translate = run = validate_descriptors = __call__ = _give


VIEW: Any = object()


def _leaf() -> Any:
    from aibi.core.schema.document import PackLeaf

    return PackLeaf.model_validate({"kind": "library.overdue"})


def _call(name: str, hook: Hook[Giving]) -> object:
    """``name``'s copier called through ``hook``, as its site calls it."""
    if name == "pack_refusals":
        return hook.call(lambda h: pack_refusals(h.validate_descriptors, VIEW, pack=PACK))
    if name == "segments":
        leaf = _leaf()
        return hook.call(lambda h: segments(h.summary, leaf))
    if name == "expansion":
        leaf = _leaf()
        return hook.call(lambda h: expansion(h.compile, leaf, VIEW, "1.0", pack=PACK))
    if name == "caveat_codes":
        form: dict[str, Any] = {}
        return hook.call(lambda h: caveat_codes(h, VIEW, form, codes=CODES))
    if name == "facet_values":
        return hook.call(lambda h: facet_values(h, VIEW))
    if name == "translation":
        allowance = guards.allowance(1_000, 10_000, 1_000, NAMES)
        return hook.call(lambda h: translation(h.translate, {}, allowance=allowance))
    if name == "values":
        allowance = guards.allowance(1_000, 10_000, 1_000, NAMES)
        return hook.call(lambda h: values(h.run, VIEW, allowance=allowance, ends=None))
    if name == "proposals":
        allowances = guards.allowances(1_000, 10_000, 1_000, NAMES)
        return hook.call(lambda h: proposals(h, VIEW, pack=PACK, allowances=allowances))
    if name == "is_true":
        return hook.call(lambda h: is_true(h, VIEW))
    raise AssertionError(name)


def _clauses() -> list[Any]:
    return [
        ValueLeaf.model_validate(
            {"kind": "value", "column": "loans.days", "range": {"gt": 14}, "negate": True}
        ),
        AllClause.model_validate(
            {
                "all": [
                    {
                        "kind": "exists",
                        "table": "loans",
                        "via": [{"rel": "rel:loans.member_id", "dir": "down"}],
                        "where": [{"kind": "value", "column": "loans.kind", "values": ["a"]}],
                    }
                ]
            }
        ),
    ]


VALID: dict[str, Callable[[], object]] = {
    "pack_refusals": lambda: [
        Refusal(
            code="library.LATE",
            path="/loans/fields",
            message=[text("late "), data("loans")],
            alternatives=[text("x")],
        )
    ],
    "segments": lambda: [text("loans returned "), data("late")],
    "expansion": _clauses,
    "caveat_codes": lambda: ["library.LATE"],
    "facet_values": lambda: {"region": ["north", "south"]},
    "translation": lambda: (
        {"aibi": "1", "cohorts": {"c": {"all": [{"kind": "value", "values": [1, 2.5]}]}}},
        [PackNote("/cohorts/c", [text("a note"), data("c")])],
    ),
    "values": lambda: {"positions": [{"a": [1, 2.5, "x", None, True]}], "view": {"b": {}}},
    "proposals": lambda: [
        Proposal("sites", "/definition", {"text": ["Places", 1]}, evidence="said so")
    ],
}
COPIED = sorted(VALID)


# --- What each copier gives for a valid value -------------------------------------------------


def test_each_copier_copies_what_is_valid() -> None:
    found = {name: _call(name, Hook(Giving(VALID[name]), PACK, "x")) for name in COPIED}
    [refusal] = cast(tuple[Refusal, ...], found["pack_refusals"])
    assert refusal.code == "library.LATE"
    assert refusal.message == [text("late "), data("loans")]
    assert found["segments"] == (text("loans returned "), data("late"))
    expanded = found["expansion"]
    assert isinstance(expanded, Expansion)
    assert list(expanded.clauses) == _clauses()
    assert found["caveat_codes"] == ("library.LATE",)
    assert found["facet_values"] == {"region": ("north", "south")}
    document, notes = cast(tuple[Any, Any], found["translation"])
    assert document["cohorts"]["c"]["all"][0]["values"] == [1, 2.5]
    assert [(n.pointer, n.translated, n.message) for n in notes] == [
        ("/cohorts/c", False, [text("a note"), data("c")])
    ]
    assert found["values"] == {"positions": [{"a": [1, 2.5, "x", None, True]}], "view": {"b": {}}}
    [item] = cast(tuple[Any, ...], found["proposals"])
    assert item.proposal is not None
    assert item.proposal.value == {"text": ["Places", 1]}
    assert Hook(Giving(lambda: True), PACK, "x").call(lambda h: is_true(h, VIEW)) is True
    assert Hook(Giving(lambda: 1), PACK, "x").call(lambda h: is_true(h, VIEW)) is False
    for value in found.values():
        provenance.core_made(value)


# --- The adversarial values ------------------------------------------------------------------


class _Text(str):
    __slots__ = ()


class _FieldsSet(set[str]):
    """A fields set whose reads record that they ran (P2)."""

    def __contains__(self, name: object) -> bool:
        _RAN.append("fields set read")
        return set.__contains__(self, name)

    def __iter__(self) -> Iterator[str]:
        _RAN.append("fields set read")
        return set.__iter__(self)


class _Dict(dict[str, Any]):
    """An instance dictionary whose reads record that they ran (P3)."""

    def values(self) -> Any:
        _RAN.append("dict read")
        return dict.values(self)

    def items(self) -> Any:
        _RAN.append("dict read")
        return dict.items(self)

    def __getitem__(self, key: str) -> Any:
        _RAN.append("dict read")
        return dict.__getitem__(self, key)


class _Colliding(str):
    """A key of a fake member's instance dictionary that hashes as ``_value_`` and records its
    comparisons, so that reading the member's value runs the pack's code (round 2's Q3)."""

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        _RAN.append("key compared")
        return str.__eq__(self, other)

    def __hash__(self) -> int:
        return hash("_value_")


def _fake_code() -> Any:
    fake = str.__new__(RefusalCode, "INVALID_VALUE")
    before = len(_RAN)
    held = {_Colliding("x"): 1, "_value_": "INVALID_VALUE", "_name_": "INVALID_VALUE"}
    del _RAN[before:]  # building it compares the keys: the pack's own code, not the core's
    object.__setattr__(fake, "__dict__", held)
    return fake


def _with_fields_set(segment: Any) -> Any:
    object.__setattr__(segment, "__pydantic_fields_set__", _FieldsSet(segment.model_fields_set))
    return segment


def _with_dict(segment: Any) -> Any:
    object.__setattr__(segment, "__dict__", _Dict(vars(segment)))
    return segment


def _dag(depth: int = 40) -> Any:
    found: Any = ()
    for _ in range(depth):
        found = (found, found)
    return found


def _chain(depth: int = 100_000) -> Any:
    found: Any = []
    for _ in range(depth):
        found = [found]
    return found


def _clause_dag(depth: int = 20) -> Any:
    found: Any = ValueLeaf.model_validate({"kind": "value", "column": "a.b", "values": ["x"]})
    for _ in range(depth):
        found = AllClause.model_construct(all=[found, found])
    return [found]


def _clause_chain(depth: int = 100_000) -> Any:
    found: Any = ValueLeaf.model_validate({"kind": "value", "column": "a.b", "values": ["x"]})
    for _ in range(depth):
        found = NotClause.model_construct(not_=found)
    return [found]


class _Recording(TextSegment):
    """A segment of the pack's own class, whose serializer records that it ran."""

    ran: Any = None

    @model_serializer(mode="wrap")
    def _record(self, handler: SerializerFunctionWrapHandler) -> Any:
        _RAN.append("serializer")
        return handler(self)


_RAN: list[str] = []


def _refusal(**given: Any) -> Any:
    members: dict[str, Any] = {"code": "library.LATE", "path": None, "message": [text("x")]}
    return Refusal.model_construct(**{**members, **given})


def _held() -> Any:
    return _with_fields_set(text("x"))


def _raise_refused() -> Any:
    raise Refused([Refusal(code="library.NO_GRADE", path="/worst", message=[text("1 to 5")])])


class _Refused(Refused):
    pass


def _raise_refused_subclass() -> Any:
    raise _Refused([Refusal(code="library.NO_GRADE", path=None, message=[])])


def _raise_limit() -> Any:
    raise guards.JsonTooLarge("result_values", 1)


class _Mapping(Mapping[str, Any]):
    def __init__(self, given: dict[str, Any]) -> None:
        self.given = given

    def __getitem__(self, key: str) -> Any:
        return self.given[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.given)

    def __len__(self) -> int:
        return len(self.given)


class _Lookalike:
    descriptor = "sites"
    pointer = "/definition"
    value = "x"
    remove = False
    evidence = None


def _with_dict_dataclass(given: Any) -> Any:
    object.__setattr__(given, "__dict__", _Dict(vars(given)))
    return given


ADVERSARIAL: dict[str, dict[str, Callable[[], object]]] = {
    "pack_refusals": {
        "fake member": lambda: [_refusal(code=_fake_code())],
        "fields set": lambda: [_refusal(message=[_with_fields_set(text("x"))])],
        "dict": lambda: [_refusal(message=[_with_dict(text("x"))])],
        "notes pattern": lambda: [Refusal(code="library.LATE", path=None, message=[_held()])],
        "constructed": lambda: [
            _refusal(
                code=RefusalCode.LIMIT_EXCEEDED,
                limit=Limit.model_construct(name=_Text("x"), max=1),
            )
        ],
        "shared segment": lambda: [_refusal(message=[text("x")] * 1_000_000)],
        "deep": lambda: [_refusal(message=_chain())],
        "core code": lambda: [Refusal(code=RefusalCode.INVALID_VALUE, path=None, message=[])],
        "subclass": lambda: [_refusal(message=[_Recording(text="x")])],
        "many": lambda: [_refusal()] * (MAX_REFUSALS + 5),
    },
    "segments": {
        "fake member": lambda: [DataSegment.model_construct(data=_fake_code())],
        "fields set": lambda: [_with_fields_set(text("x"))],
        "dict": lambda: [_with_dict(text("x"))],
        "notes pattern": lambda: [_held()],
        "constructed": lambda: [TextSegment.model_construct(text=_Text("x"))],
        "dag": lambda: _dag(),
        "deep": lambda: [TextSegment.model_construct(text=_chain())],
        "subclass": lambda: [_Recording(text="x")],
    },
    "expansion": {
        "fake member": lambda: [
            ValueLeaf.model_construct(kind="value", column=_fake_code(), values=["x"])
        ],
        "fields set": lambda: [_with_fields_set(_clauses()[0])],
        "dict": lambda: [_with_dict(_clauses()[0])],
        "constructed": lambda: [ValueLeaf.model_construct(kind="value", column=_Text("a.b"))],
        "dag": _clause_dag,
        "deep": _clause_chain,
        "refused subclass": _raise_refused_subclass,
        "refused": _raise_refused,
    },
    "caveat_codes": {
        "fake member": lambda: [_fake_code()],
        "subclass": lambda: [_Text("library.LATE")],
        "undeclared": lambda: ["library.OTHER"],
        "dag": lambda: _dag(),
    },
    "facet_values": {
        "subclass keys": lambda: {_Text("region"): [_Text("north")]},
        "mapping": lambda: _Mapping({"region": ["north"]}),
        "dag": lambda: {"region": _dag()},
        "deep": lambda: {"region": _chain()},
    },
    "translation": {
        "notes pattern": lambda: ({"aibi": "1"}, [PackNote("/a", [_with_fields_set(text("x"))])]),
        "dict": lambda: ({"aibi": "1"}, [PackNote("/a", [_with_dict(text("x"))])]),
        "dag": lambda: ({"aibi": _dag()}, []),
        "deep": lambda: ({"aibi": _chain()}, []),
        "late marker": lambda: ({"aibi": Late()}, []),
        "subclass": lambda: ({"aibi": _Text("1"), "n": [_Text("x")]}, []),
    },
    "values": {
        "fake member": lambda: {"positions": [_fake_code()], "view": {}},
        "dag": lambda: _dag(),
        "deep": lambda: _chain(),
        "late marker": lambda: Late(),
        "own limit": _raise_limit,
        "subclass": lambda: {"positions": [{"a": _Text("x")}], "view": {_Text("k"): 1}},
    },
    "proposals": {
        "dag": lambda: [Proposal("sites", "/definition", _dag())],
        "deep": lambda: [Proposal("sites", "/definition", _chain())],
        "dict": lambda: [_with_dict_dataclass(Proposal("sites", "/definition", "x"))],
        "lookalike": lambda: [_Lookalike()],
        "subclass names": lambda: [Proposal(_Text("sites"), _Text("/definition"), "x")],
    },
}


_CASES = [(name, case) for name, cases in ADVERSARIAL.items() for case in cases]


@pytest.mark.parametrize(("name", "case"), _CASES, ids=[f"{n}-{c}" for n, c in _CASES])
def test_what_a_pack_made_is_its_failure_a_marker_or_a_clean_copy(name: str, case: str) -> None:
    _RAN.clear()
    giving = Giving(ADVERSARIAL[name][case])
    started = time.monotonic()
    try:
        found = _call(name, Hook(giving, PACK, "x"))
    except PackFailed:
        found = None
    assert time.monotonic() - started < 10, "a copier spent its time on what it refuses"
    assert _RAN == [], "a pack's serializer ran"
    if found is None or isinstance(found, Tripped | NotJson | Late | NoExpansion):
        return
    provenance.core_made(found)
    assert provenance.shared([*giving.kept, *giving.args], found) == []


def test_a_compiler_s_refusals_are_read_back_and_a_subclass_s_are_its_failure() -> None:
    found = _call("expansion", Hook(Giving(_raise_refused), PACK, "x"))
    assert isinstance(found, CompilerRefused)
    assert [(r.code, r.path) for r in found.refusals] == [("library.NO_GRADE", "/worst")]
    with pytest.raises(PackFailed):
        _call("expansion", Hook(Giving(_raise_refused_subclass), PACK, "x"))


def test_more_refusals_than_a_list_holds_are_read_to_one_past_it() -> None:
    found = _call("pack_refusals", Hook(Giving(ADVERSARIAL["pack_refusals"]["many"]), PACK, "x"))
    assert len(cast(tuple[Refusal, ...], found)) == MAX_REFUSALS + 1


def test_a_values_copy_names_its_own_limit_and_not_the_pack_s() -> None:
    allowance = guards.allowance(3, 100, 10, NAMES)
    found = Hook(Giving(lambda: [1, 2, 3, 4]), PACK, "x").call(
        lambda h: values(h.run, VIEW, allowance=allowance, ends=None)
    )
    assert found == Tripped("result_values", 3)
    with pytest.raises(PackFailed):
        _call("values", Hook(Giving(_raise_limit), PACK, "x"))
    late = Hook(Giving(lambda: {}), PACK, "x").call(
        lambda h: values(h.run, VIEW, allowance=allowance, ends=time.monotonic() - 1)
    )
    assert late == Late()


def test_a_value_error_of_the_pack_s_is_no_not_json() -> None:
    def raising() -> Any:
        raise guards._NotJsonError("s3cr3t")  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(PackFailed):
        _call("values", Hook(Giving(raising), PACK, "x"))

    class NotJsonItems(Mapping[str, Any]):
        """A mapping whose ``items`` raises the core's own ``_NotJsonError``, as the pack's."""

        def __getitem__(self, key: str) -> Any:
            raise KeyError(key)

        def __iter__(self) -> Iterator[str]:
            return iter(())

        def __len__(self) -> int:
            return 0

        def items(self) -> Any:
            raise guards._NotJsonError("s3cr3t")  # pyright: ignore[reportPrivateUsage]

    for name in ("translation", "values"):
        made = (lambda: (NotJsonItems(), [])) if name == "translation" else NotJsonItems
        with pytest.raises(PackFailed):
            _call(name, Hook(Giving(made), PACK, "x"))
    own = _call("translation", Hook(Giving(lambda: ({"a": float("nan")}, [])), PACK, "x"))
    assert own == NotJson()

    class Reading(Mapping[str, Any]):
        def __getitem__(self, key: str) -> Any:
            raise KeyError(key)

        def __iter__(self) -> Iterator[str]:
            return iter(())

        def __len__(self) -> int:
            return 0

        def items(self) -> Any:
            raise ValueError("s3cr3t")

    with pytest.raises(PackFailed):
        _call("values", Hook(Giving(Reading), PACK, "x"))
    with pytest.raises(PackFailed):
        _call("translation", Hook(Giving(lambda: (Reading(), [])), PACK, "x"))
    found = _call("values", Hook(Giving(lambda: {"a": float("nan")}), PACK, "x"))
    assert found == NotJson()


def test_one_proposal_s_failure_skips_it_alone() -> None:
    class Raising(Mapping[str, Any]):
        def __getitem__(self, key: str) -> Any:
            raise KeyError(key)

        def __iter__(self) -> Iterator[str]:
            raise KeyError("x")

        def __len__(self) -> int:
            return 1

        def items(self) -> Any:
            raise KeyError("s3cr3t")

    def made() -> Any:
        return [
            Proposal("sites", "/definition", cast(Any, Raising())),
            Proposal("sites", "/definition", _dag()),
            Proposal("sites", "/label", "Places"),
            _Lookalike(),
        ]

    allowances = guards.allowances(1_000, 10_000, 1_000, NAMES)
    found = Hook(Giving(made), PACK, "x").call(
        lambda h: proposals(h, VIEW, pack=PACK, allowances=allowances)
    )
    assert [(i.descriptor, i.pointer, i.code) for i in found] == [
        ("sites", "/definition", "PACK_FAILED"),
        ("sites", "/definition", "INVALID_VALUE"),
        ("sites", "/label", None),
        (UNSHOWN, UNSHOWN, "INVALID_VALUE"),
    ]
    assert found[2].proposal is not None
    for passing in (MemoryError, KeyboardInterrupt, SystemExit):

        class Passing(Mapping[str, Any]):
            def __getitem__(self, key: str) -> Any:
                raise KeyError(key)

            def __iter__(self) -> Iterator[str]:
                raise KeyError("x")

            def __len__(self) -> int:
                return 1

            def items(self, kind: type[BaseException] = passing) -> Any:
                raise kind("s3cr3t")

        given = Giving(lambda: [Proposal("sites", "/definition", cast(Any, Passing()))])
        with pytest.raises(passing) as raised:
            Hook(given, PACK, "x").call(
                lambda h: proposals(h, VIEW, pack=PACK, allowances=allowances)
            )
        assert "s3cr3t" not in str(raised.value)


def test_an_allowance_one_proposal_trips_is_its_own() -> None:
    allowances = guards.allowances(2, 100, 10, NAMES)

    def made() -> Any:
        return [
            Proposal("sites", "/definition", [1, 2, 3]),
            Proposal("sites", "/label", "ok"),
        ]

    found = Hook(Giving(made), PACK, "x").call(
        lambda h: proposals(h, VIEW, pack=PACK, allowances=allowances)
    )
    assert [i.code for i in found] == ["INVALID_VALUE", None]


def _not_json(n: int) -> Callable[[], object]:
    """The review's probe of round 2 (m2): ``n`` proposals whose value is ``nan``."""
    return lambda: [Proposal("sites", "/label", float("nan")) for _ in range(n)]


def _propose(make: Callable[[], object]) -> tuple[copiers.ProposalItem, ...]:
    allowances = guards.allowances(1_000, 10_000, 1_000, NAMES)
    return Hook(Giving(make), PACK, "x").call(
        lambda h: proposals(h, VIEW, pack=PACK, allowances=allowances)
    )


def test_a_proposer_gives_at_most_a_queue_s_items_and_more_is_its_failure() -> None:
    started = time.monotonic()
    with pytest.raises(PackFailed):
        _propose(_not_json(40_000))
    assert time.monotonic() - started < 5
    with pytest.raises(PackFailed):
        _propose(_not_json(MAX_QUEUE_ITEMS + 1))
    found = _propose(_not_json(MAX_QUEUE_ITEMS))
    assert {i.code for i in found} == {"INVALID_VALUE"}
    assert len(found) == MAX_QUEUE_ITEMS


def test_proposals_that_are_not_json_are_read_in_linear_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The review's probe of round 2 (m2), past the cap: 40,000 proposals of ``nan`` took 36.7 s
    with the set's linear check, and take under a second with its check by ``id``."""
    monkeypatch.setattr(copiers, "MAX_QUEUE_ITEMS", 40_000)
    started = time.monotonic()
    found = _propose(_not_json(40_000))
    assert time.monotonic() - started < 10
    assert len(found) == 40_000
    assert {i.code for i in found} == {"INVALID_VALUE"}


# --- The protocols as the copiers take them (D388's behaviour changes) -------------------------


class _Sequence(Sequence[Any]):
    """A ``Sequence`` of the pack's own, which the protocols no longer take."""

    def __init__(self, items: list[Any]) -> None:
        self.items = items

    def __getitem__(self, index: Any) -> Any:
        return self.items[index]

    def __len__(self) -> int:
        return len(self.items)


class _RefusalSubclass(Refusal):
    pass


def _late() -> Refusal:
    return Refusal(code="library.LATE", path=None, message=[text("late")])


def _generator(make: Callable[[], list[Any]]) -> Callable[[], object]:
    return lambda: (item for item in make())


_SHAPES: dict[str, tuple[str, Callable[[], object], Callable[[], object]]] = {
    "validator, a generator": ("pack_refusals", _generator(lambda: [_late()]), lambda: (_late(),)),
    "validator, a sequence": ("pack_refusals", lambda: _Sequence([_late()]), lambda: [_late()]),
    "validator, a refusal subclass": (
        "pack_refusals",
        lambda: [_RefusalSubclass(code="library.LATE", path=None, message=[])],
        lambda: [_late()],
    ),
    "ontology validator, a match": (
        "is_true",
        lambda: re.fullmatch(r"M\d+", "M1"),
        lambda: True,
    ),
    "predicate, truthy": ("is_true", lambda: 1, lambda: True),
    "compiler, a refused subclass": ("expansion", _raise_refused_subclass, _raise_refused),
    "compiler, a generator": ("expansion", _generator(_clauses), lambda: tuple(_clauses())),
    "summary, a generator": ("segments", _generator(lambda: [text("x")]), lambda: (text("x"),)),
    "caveat rule, a generator": (
        "caveat_codes",
        _generator(lambda: ["library.LATE"]),
        lambda: ("library.LATE",),
    ),
    "facet, a read-only mapping": (
        "facet_values",
        lambda: MappingProxyType({"region": ("north",)}),
        lambda: {"region": ("north",)},
    ),
    "facet, a sequence of values": (
        "facet_values",
        lambda: {"region": _Sequence(["north"])},
        lambda: {"region": ["north"]},
    ),
    "translator, a list": ("translation", lambda: [{"aibi": "1"}, []], lambda: ({"aibi": "1"}, ())),
    "translator, notes in a generator": (
        "translation",
        lambda: ({"aibi": "1"}, (n for n in [PackNote("/a", [text("x")])])),
        lambda: ({"aibi": "1"}, (PackNote("/a", (text("x"),)),)),
    ),
    "proposer, a generator": (
        "proposals",
        _generator(lambda: [Proposal("sites", "/label", "Places")]),
        lambda: (Proposal("sites", "/label", "Places"),),
    ),
}


@pytest.mark.parametrize("case", sorted(_SHAPES))
def test_an_old_shape_is_refused_and_the_protocol_s_is_taken(case: str) -> None:
    name, old, new = _SHAPES[case]
    try:
        before = _call(name, Hook(Giving(old), PACK, "x"))
    except PackFailed:
        before = None
    assert before is None or before is False or isinstance(before, NoExpansion), before
    after = _call(name, Hook(Giving(new), PACK, "x"))
    assert after is not None
    assert after is not False
    assert not isinstance(after, NoExpansion)
    if name == "proposals":
        [item] = cast(tuple[Any, ...], after)
        assert item.proposal is not None
    provenance.core_made(after)


# --- The limits a copier names, and the ones it does not --------------------------------------


@pytest.mark.parametrize("name", ["pack_refusals", "expansion"])
def test_a_refusal_naming_a_limit_or_in_another_pack_s_code_is_the_hook_s_failure(
    name: str,
) -> None:
    def refusing(**given: Any) -> Callable[[], object]:
        made = Refusal.model_construct(
            **{"code": "library.LATE", "path": None, "message": [text("x")], **given}
        )
        if name == "pack_refusals":
            return lambda: [made]

        def raising() -> Any:
            raise Refused([made])

        return raising

    found = _call(name, Hook(Giving(refusing()), PACK, "x"))
    assert found is not None
    for wrong in (
        {"limit": Limit(name="json_values", max=1)},
        {"code": "other.LATE"},
        {"code": RefusalCode.INVALID_VALUE},
        {"counts": []},
    ):
        with pytest.raises(PackFailed):
            _call(name, Hook(Giving(refusing(**wrong)), PACK, "x"))


class _RaisingItems(Mapping[str, Any]):
    """A mapping whose ``items`` raises a limit's error of the core's type, as the pack's own."""

    def __getitem__(self, key: str) -> Any:
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def items(self) -> Any:
        raise guards.JsonTooLarge("json_values", 1)


@pytest.mark.parametrize("name", ["translation", "values", "proposals"])
def test_a_limit_the_pack_raises_while_it_is_copied_is_its_failure_not_a_limit(
    name: str,
) -> None:
    allowance = guards.allowance(1_000, 10_000, 1_000, NAMES)
    if name == "proposals":
        allowances = guards.allowances(1_000, 10_000, 1_000, NAMES)
        given = Giving(lambda: [Proposal("sites", "/label", cast(Any, _RaisingItems()))])
        [item] = Hook(given, PACK, "x").call(
            lambda h: proposals(h, VIEW, pack=PACK, allowances=allowances)
        )
        assert (item.code, item.proposal) == ("PACK_FAILED", None)
    elif name == "translation":
        hook = Hook(Giving(lambda: (_RaisingItems(), [])), PACK, "x")
        with pytest.raises(PackFailed):
            hook.call(lambda h: translation(h.translate, {}, allowance=allowance))
        own = guards.allowance(2, 10_000, 1_000, NAMES)
        tripped = Hook(Giving(lambda: ({"aibi": "1", "x": [1, 2]}, [])), PACK, "x").call(
            lambda h: translation(h.translate, {}, allowance=own)
        )
        assert tripped == Tripped("result_values", 2)
    else:
        hook = Hook(Giving(lambda: {"a": _RaisingItems()}), PACK, "x")
        with pytest.raises(PackFailed):
            hook.call(lambda h: values(h.run, VIEW, allowance=allowance, ends=None))


# --- The expansion's count walk ---------------------------------------------------------------


def _ratio(model: type[BaseModel]) -> float:
    """The most values the count walk counts per JSON value a model's instances write (D388):
    ``1 + (F - r) / (1 + r)`` of ``F`` members, ``r`` of them required."""
    members = model.model_fields
    required = sum(1 for info in members.values() if info.is_required())
    for info in members.values():
        if not info.is_required():
            assert info.default is None or type(info.default) is bool, (model, info)
    return 1 + (len(members) - required) / (1 + required)


def test_the_expansion_s_walk_bound_is_derived_from_the_clause_models() -> None:
    models = copiers._CLAUSE_MODELS  # pyright: ignore[reportPrivateUsage]
    assert math.ceil(max(_ratio(model) for model in models)) == EXPANSION_RATIO
    assert copiers._EXPANSION_VALUES == EXPANSION_RATIO * MAX_VALUES  # pyright: ignore[reportPrivateUsage]


def _exists(n: int) -> list[Any]:
    leaf = {"kind": "exists", "table": "t"}
    return [ExistsLeaf.model_validate(leaf) for _ in range(n)]


@pytest.mark.parametrize("n", [50_000, (MAX_VALUES - 1) // 3])
def test_an_expansion_within_a_document_s_json_values_passes_the_walk(n: int) -> None:
    leaves = _exists(n)
    found = _call("expansion", Hook(Giving(lambda: leaves), PACK, "x"))
    assert isinstance(found, Expansion)
    assert len(found.clauses) == n
    written = cast(list[Any], found.written)
    assert 1 + 3 * len(written) <= MAX_VALUES


def _codes(leaves: int = 15, codes: int = 10_000, width: int = 15) -> list[Any]:
    """The review's probe of round 2 (m1): a concept expanded to code lists, ``leaves`` value
    leaves of ``codes`` codes of ``width`` characters each."""
    return [
        ValueLeaf.model_validate(
            {
                "kind": "value",
                "column": "loans.code",
                "values": [f"{leaf:02d}-{code:0{width - 3}d}" for code in range(codes)],
            }
        )
        for leaf in range(leaves)
    ]


def test_an_expansion_s_characters_are_bounded_only_as_the_core_s_own_limits_bound_them() -> None:
    leaves = _codes()
    assert all(len(code) == 15 for leaf in leaves for code in leaf.values)
    found = _call("expansion", Hook(Giving(lambda: leaves), PACK, "x"))
    assert isinstance(found, Expansion)
    written = cast(list[Any], found.written)
    assert sum(len(code) for leaf in written for code in leaf["values"]) == 2_250_000
    assert sys.maxsize == copiers._EXPANSION_CHARACTERS  # pyright: ignore[reportPrivateUsage]


def _strings(schema: object) -> Iterator[dict[str, Any]]:
    if isinstance(schema, dict):
        found = cast(dict[str, Any], schema)
        if found.get("type") == "string":
            yield found
        for member in found.values():
            yield from _strings(member)
    elif isinstance(schema, list):
        for member in cast(list[object], schema):
            yield from _strings(member)


def test_every_string_of_a_clause_holds_at_most_the_walk_s_bound_of_one_string() -> None:
    """The walk's per-string bound, ``MAX_STRING``, refuses no string a clause may hold: every
    string member of the clause models is a constant of at most ``MAX_STRING`` characters, an
    identifier, a pattern of fewer, or an enumeration."""
    schema = TypeAdapter(list[Clause]).json_schema()
    for string in _strings(schema):
        if "enum" in string or "const" in string:
            continue
        bounded = string.get("maxLength")
        if bounded is None:
            assert re.fullmatch(r"\^\[[^\]]+\]\{1,64\}\$", string["pattern"]), string
        else:
            assert bounded <= MAX_STRING, string


def test_an_expansion_s_string_past_a_constant_s_bound_is_the_walk_s_own_refusal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    within = ValueLeaf.model_validate({"kind": "value", "column": "a.b", "values": ["x"]})
    past = ValueLeaf.model_construct(
        _fields_set={"kind", "column", "values"},
        kind="value",
        column="a.b",
        values=["x" * (MAX_STRING + 1)],
    )
    found = _call("expansion", Hook(Giving(lambda: [within]), PACK, "x"))
    assert isinstance(found, Expansion)
    with pytest.raises(Unfit, match="more than the core takes"):
        expansion(lambda *given: [past], _leaf(), VIEW, "1.0", pack=PACK)
    with caplog.at_level(logging.WARNING), pytest.raises(PackFailed):
        _call("expansion", Hook(Giving(lambda: [past]), PACK, "compiler"))
    assert caplog.messages == ["pack library: its compiler gave what the core does not take"]


def _nested(depth: int) -> list[Any]:
    found: Any = ValueLeaf.model_validate({"kind": "value", "column": "a.b", "values": ["x"]})
    for _ in range(depth):
        found = NotClause.model_construct(not_=found)
    return [found]


def test_an_expansion_just_past_the_depth_is_the_walk_s_own_refusal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    within = _call("expansion", Hook(Giving(lambda: _nested(MAX_DEPTH - 4)), PACK, "x"))
    assert isinstance(within, Expansion | NoExpansion)
    past = _nested(MAX_DEPTH)
    with pytest.raises(Unfit, match=f"nesting deeper than {MAX_DEPTH}"):
        expansion(lambda *given: past, _leaf(), VIEW, "1.0", pack=PACK)
    with caplog.at_level(logging.WARNING), pytest.raises(PackFailed):
        _call("expansion", Hook(Giving(lambda: past), PACK, "compiler"))
    assert caplog.messages == ["pack library: its compiler gave what the core does not take"]


def test_a_model_with_extra_or_private_members_is_not_its_own() -> None:
    fields = copiers._fields  # pyright: ignore[reportPrivateUsage]
    for member in ("__pydantic_extra__", "__pydantic_private__"):
        leaf = _clauses()[0]
        assert fields(leaf, model=True) is not None
        object.__setattr__(leaf, member, {"x": 1})
        assert fields(leaf, model=True) is None
        with pytest.raises(PackFailed):
            _call("expansion", Hook(Giving(lambda leaf=leaf: [leaf]), PACK, "x"))


# --- What the log says of the copiers' own refusals -------------------------------------------


_UNTAKEN: dict[str, tuple[str, Callable[[], object]]] = {
    "a value of a type the walk does not take": (
        "segments",
        lambda: [TextSegment.model_construct(text=object())],
    ),
    "a summary past its characters": ("segments", lambda: [text("x" * (MAX_TEXT + 1))]),
    "a summary past its characters together": (
        "segments",
        lambda: [text("x" * 6_000), text("y" * 6_000)],
    ),
    "a shared segment": ("pack_refusals", lambda: [_refusal(message=[text("x")] * 1_000)]),
    "a read-back that fails": ("pack_refusals", lambda: [_refusal(path="no pointer")]),
    "an expansion's DAG": ("expansion", _clause_dag),
    "not a list": ("caveat_codes", lambda: "library.LATE"),
}


def test_a_summary_holds_at_most_a_note_s_characters_together() -> None:
    found = _call("segments", Hook(Giving(lambda: [text("x" * 5_000)] * 2), PACK, "x"))
    assert found == (text("x" * 5_000), text("x" * 5_000))
    with pytest.raises(PackFailed):
        _call("segments", Hook(Giving(lambda: [text("x" * 5_000), text("y" * 5_001)]), PACK, "x"))


@pytest.mark.parametrize("case", sorted(_UNTAKEN))
def test_every_refusal_of_a_copier_s_own_is_logged_as_what_the_core_does_not_take(
    case: str, caplog: pytest.LogCaptureFixture
) -> None:
    name, make = _UNTAKEN[case]
    with caplog.at_level(logging.WARNING), pytest.raises(PackFailed):
        _call(name, Hook(Giving(make), PACK, "stage"))
    assert caplog.messages == ["pack library: its stage gave what the core does not take"]


# --- What a proposal names, and removes -------------------------------------------------------


def test_a_proposal_s_names_are_shown_only_as_bounded_text() -> None:
    long = "s" * (MAX_STRING + 1)

    def made() -> Any:
        return [
            Proposal(long, "/label", cast(Any, object())),
            Proposal(cast(Any, 3), "/label", "x"),
            Proposal("sites", "\ud800", "x"),
            Proposal(_Text("sites"), "", cast(Any, object())),
        ]

    allowances = guards.allowances(1_000, 10_000, 1_000, NAMES)
    found = Hook(Giving(made), PACK, "x").call(
        lambda h: proposals(h, VIEW, pack=PACK, allowances=allowances)
    )
    assert [(i.descriptor, i.pointer, i.code) for i in found] == [
        (UNSHOWN, "/label", "INVALID_VALUE"),
        (UNSHOWN, "/label", "INVALID_VALUE"),
        ("sites", UNSHOWN, "INVALID_VALUE"),
        ("sites", "", "INVALID_VALUE"),
    ]
    assert all(
        (type(name) is str or name is UNSHOWN) for i in found for name in (i.descriptor, i.pointer)
    )


class _Unreadable(Mapping[str, Any]):
    """A value whose ``items`` raises, so that reading it is the proposal's failure."""

    def __getitem__(self, key: str) -> Any:
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 1

    def items(self) -> Any:
        raise KeyError("s3cr3t")


def test_a_failed_proposal_s_names_are_shown_only_as_bounded_text() -> None:
    long = "s" * (MAX_STRING + 1)

    def made() -> Any:
        return [
            Proposal(long, "\ud800", cast(Any, _Unreadable())),
            Proposal(cast(Any, 3), "/label", cast(Any, _Unreadable())),
            Proposal(_Text("sites"), "/label", cast(Any, _Unreadable())),
        ]

    allowances = guards.allowances(1_000, 10_000, 1_000, NAMES)
    found = Hook(Giving(made), PACK, "x").call(
        lambda h: proposals(h, VIEW, pack=PACK, allowances=allowances)
    )
    assert [(i.descriptor, i.pointer, i.code) for i in found] == [
        (UNSHOWN, UNSHOWN, "PACK_FAILED"),
        (UNSHOWN, "/label", "PACK_FAILED"),
        ("sites", "/label", "PACK_FAILED"),
    ]
    assert type(found[2].descriptor) is str


def test_the_core_s_words_for_a_name_are_its_own_segment_never_the_pack_s_text() -> None:
    words = UNSHOWN.text
    allowances = guards.allowances(1_000, 10_000, 1_000, NAMES)
    given = Giving(lambda: [Proposal(words, words, cast(Any, object()))])
    [item] = Hook(given, PACK, "x").call(
        lambda h: proposals(h, VIEW, pack=PACK, allowances=allowances)
    )
    assert (item.descriptor, item.pointer) == (words, words)
    assert type(item.descriptor) is str
    assert item.descriptor != UNSHOWN
    assert type(UNSHOWN) is TextSegment


def test_only_true_itself_proposes_a_removal() -> None:
    allowances = guards.allowances(1_000, 10_000, 1_000, NAMES)
    for remove, removes in ((True, True), (1, False), ("yes", False), (False, False)):
        given = Giving(lambda remove=remove: [Proposal("sites", "/label", "x", cast(Any, remove))])
        [item] = Hook(given, PACK, "x").call(
            lambda h: proposals(h, VIEW, pack=PACK, allowances=allowances)
        )
        assert item.proposal is not None
        assert item.proposal.remove is removes
        if not removes:
            assert item.proposal.value == "x"


# --- Provenance, calibrated per copier -------------------------------------------------------


@pytest.mark.parametrize("name", COPIED)
def test_two_independent_copies_share_nothing_the_walk_does_not_allow(name: str) -> None:
    first = _call(name, Hook(Giving(VALID[name]), PACK, "x"))
    second = _call(name, Hook(Giving(VALID[name]), PACK, "x"))
    assert len(provenance.reachable([first])) > 1, "the walk reached nothing"
    assert provenance.shared([first], second) == []


@pytest.mark.parametrize("name", COPIED)
def test_a_copy_shares_nothing_mutable_with_what_the_pack_built_or_was_given(name: str) -> None:
    giving = Giving(VALID[name])
    found = _call(name, Hook(giving, PACK, "x"))
    assert provenance.shared([*giving.kept, *giving.args], found) == []
    assert provenance.holds_no_handle(giving.args)


def test_the_walk_finds_what_a_copier_that_keeps_the_pack_s_list_shares() -> None:
    kept: list[str] = ["library.LATE"]
    assert provenance.shared([kept], {"x": kept}) != []


# --- Keep and mutate -------------------------------------------------------------------------


def _mutate(value: object, seen: set[int] | None = None) -> None:
    """Change everything the pack kept, all the way down."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    if type(value) is list:
        items = list(cast(list[object], value))
        cast(list[object], value).append("changed")
        for item in items:
            _mutate(item, seen)
    elif type(value) is dict:
        given = cast(dict[str, object], value)
        items = list(given.values())
        given["changed"] = "changed"
        for item in items:
            _mutate(item, seen)
    elif type(value) is tuple:
        for item in cast(tuple[object, ...], value):
            _mutate(item, seen)
    elif isinstance(value, BaseModel):
        held = vars(value)
        for item in list(held.values()):
            _mutate(item, seen)
        value.__pydantic_fields_set__.add("changed")
    elif hasattr(value, "__dict__"):
        held = vars(value)
        for item in list(held.values()):
            _mutate(item, seen)


def _snapshot(value: object) -> object:
    """A copy's content: its models by their class serializer's JSON of the members given, with
    their fields sets, and its containers item by item."""
    if isinstance(value, BaseModel):
        dumped = type(value).__pydantic_serializer__.to_json(value, exclude_unset=True)
        return (type(value).__name__, dumped, frozenset(value.__pydantic_fields_set__))
    if type(value) is dict:
        return {k: _snapshot(v) for k, v in cast(dict[object, object], value).items()}
    if type(value) is list or type(value) is tuple:
        return [_snapshot(v) for v in cast(list[object], value)]
    if hasattr(value, "__dataclass_fields__"):
        return {k: _snapshot(v) for k, v in vars(value).items()}
    return value


@pytest.mark.parametrize("name", COPIED)
def test_what_the_pack_changes_after_the_call_changes_nothing_the_core_holds(name: str) -> None:
    giving = Giving(VALID[name])
    found = _call(name, Hook(giving, PACK, "x"))
    before = _snapshot(found)
    for kept in giving.kept:
        _mutate(kept)
    assert _snapshot(found) == before


# --- No state --------------------------------------------------------------------------------


def _state(module: Any) -> dict[str, object]:
    found: dict[str, object] = {}
    for name, value in vars(module).items():
        if type(value) in (dict, set, list):
            found[name] = (len(value), [id(item) for item in value])
    return found


def test_the_copiers_keep_no_state() -> None:
    modules = [
        importlib.import_module("aibi.core.schema.copiers"),
        importlib.import_module("aibi.core.schema.guards"),
        importlib.import_module("aibi.core.schema.pack_api"),
    ]
    for name in COPIED:
        _call(name, Hook(Giving(VALID[name]), PACK, "x"))
    before = [_state(module) for module in modules]
    first = {name: _call(name, Hook(Giving(VALID[name]), PACK, "x")) for name in COPIED}
    second = {name: _call(name, Hook(Giving(VALID[name]), PACK, "x")) for name in COPIED}
    assert [_state(module) for module in modules] == before
    for name in COPIED:
        assert provenance.shared([first[name]], second[name]) == []


def test_no_copier_is_cached() -> None:
    for copier in copiers.COPIERS:
        assert not hasattr(copier, "cache_info")
        assert inspect.isfunction(copier)


def test_the_dynamic_walk_finds_what_is_not_the_core_s() -> None:
    for wrong in (
        {"code": _fake_code()},
        [_with_fields_set(text("x"))],
        [_with_dict(text("x"))],
        [_Text("x")],
        _Recording(text="x"),
    ):
        with pytest.raises(AssertionError):
            provenance.core_made(wrong)
    provenance.core_made([text("x"), RefusalCode.INVALID_VALUE, {"a": (1, 2.5, None)}])


# --- The enum table --------------------------------------------------------------------------


def test_the_enum_table_is_every_enum_of_the_schema() -> None:
    table = copiers._ENUMS  # pyright: ignore[reportPrivateUsage]
    assert set(table) == set(provenance.SCHEMA_ENUMS)
    assert len(table) == len(set(table))

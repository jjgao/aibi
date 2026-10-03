"""Registration as a copy of the core's (SPEC §10.1, D201, D285, D402).

The registry reads a pack-made object only while it registers the pack, inside the guard of the
member that holds it, each thing once; afterwards it holds core-made copies (the manifest,
concepts, entries, schemas, names, wordings) and the pack's hook objects by identity, which it
never reads again. What a read raises is that member's problem in the core's words, the pack's
other members still read; ``MemoryError``, ``KeyboardInterrupt`` and ``SystemExit`` themselves
stop registration, raised again as new instances.
"""

import ast
import dataclasses
import inspect
import textwrap
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import Any, ClassVar, cast

import pytest
from pydantic import BaseModel
from tests.core.schema.test_pack_api import (
    ARCHIVE,
    CORE,
    LIBRARY,
    LoanRates,
    Marc,
    Overdue,
    _Fixed,  # pyright: ignore[reportPrivateUsage]
    concept,
    entry,
    manifest,
)

from aibi.core.schema import pack_api
from aibi.core.schema.caveats import CaveatCode, Severity
from aibi.core.schema.descriptors import AnalysisDescriptor, ConceptDescriptor
from aibi.core.schema.guards import Hook
from aibi.core.schema.jsonschemas import Checker
from aibi.core.schema.pack_api import (
    Pack,
    PackError,
    PackInfo,
    PackManifest,
    PackRegistry,
    RegisteredAnalysis,
)

SECRET = "s3cr3t-pack-text"


def _problems(*packs: object) -> list[str]:
    with pytest.raises(PackError) as raised:
        PackRegistry(cast(Any, packs), core_version=CORE)
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    return list(raised.value.problems)


def _secretless(problems: list[str]) -> None:
    assert all(SECRET not in problem for problem in problems), problems


# --- The Pack itself --------------------------------------------------------------------------


def test_a_pack_that_is_not_exactly_a_pack_is_refused_and_never_read() -> None:
    reads: list[str] = []

    @dataclasses.dataclass(frozen=True)
    class Counting(Pack):
        def __getattribute__(self, name: str) -> Any:
            reads.append(name)
            return super().__getattribute__(name)

    given = Counting(**{f.name: getattr(LIBRARY, f.name) for f in dataclasses.fields(Pack)})
    reads.clear()
    assert _problems(given, ARCHIVE) == ["the 1st pack given is not a Pack"]
    assert reads == []


def test_the_kept_and_handed_out_packs_are_core_made() -> None:
    packs = PackRegistry([LIBRARY, ARCHIVE], core_version=CORE)
    for pack in (packs.pack("library"), *packs.listed(["library", "archive"])):
        assert type(pack) is PackInfo
        assert type(pack.manifest) is PackManifest
        assert all(type(c) is ConceptDescriptor for c in pack.concepts)


# --- Read once, checks on the copy -------------------------------------------------------------


class _Flipping:
    """An analysis whose entry is valid on its first read and refers to a remote schema on
    every read after."""

    def __init__(self) -> None:
        self.reads = 0

    @property
    def entry(self) -> AnalysisDescriptor:
        self.reads += 1
        found = entry("library.loan_rates")
        if self.reads == 1:
            return found
        fields = found.fields.model_copy(update={"params": {"$ref": "https://example.org/x"}})
        return found.model_copy(update={"fields": fields})

    def run(self, inputs: Any) -> Mapping[str, Any]:
        return {}


def test_an_entry_is_read_once_and_checked_as_read() -> None:
    flipping = _Flipping()
    packs = PackRegistry([replace(LIBRARY, analyses=(flipping,)), ARCHIVE], core_version=CORE)
    assert flipping.reads == 1
    found = packs.analysis("library.loan_rates")
    assert found is not None
    assert found.entry.fields.params == {"type": "object"}
    params, _ = packs.analysis_checkers("library.loan_rates")
    assert params.failures({}) == []
    assert flipping.reads == 1


class _Counted(Mapping[str, Any]):
    """A mapping that counts every way of reading it."""

    def __init__(self, given: Mapping[str, Any]) -> None:
        self.given = dict(given)
        self.reads: list[str] = []

    def __getitem__(self, key: str) -> Any:
        self.reads.append("__getitem__")
        return self.given[key]

    def __iter__(self) -> Iterator[str]:
        self.reads.append("__iter__")
        return iter(self.given)

    def __len__(self) -> int:
        self.reads.append("__len__")
        return len(self.given)

    def __contains__(self, key: object) -> bool:
        self.reads.append("__contains__")
        return key in self.given

    def keys(self) -> Any:
        self.reads.append("keys")
        return self.given.keys()

    def items(self) -> Any:
        self.reads.append("items")
        return self.given.items()

    def values(self) -> Any:
        self.reads.append("values")
        return self.given.values()

    def get(self, key: str, default: Any = None) -> Any:
        self.reads.append("get")
        return self.given.get(key, default)

    def __bool__(self) -> bool:
        self.reads.append("__bool__")
        return bool(self.given)


class _CountedSequence(Sequence[Any]):
    def __init__(self, given: Sequence[Any]) -> None:
        self.given = list(given)
        self.reads: list[str] = []

    def __getitem__(self, index: Any) -> Any:
        self.reads.append("__getitem__")
        return self.given[index]

    def __iter__(self) -> Iterator[Any]:
        self.reads.append("__iter__")
        return iter(self.given)

    def __len__(self) -> int:
        self.reads.append("__len__")
        return len(self.given)

    def __bool__(self) -> bool:
        self.reads.append("__bool__")
        return bool(self.given)


_MAPPINGS = (
    "ontology_systems",
    "extension_schemas",
    "leaf_kinds",
    "translators",
    "requirement_predicates",
    "caveat_codes",
    "wording",
)


@pytest.mark.parametrize("member", _MAPPINGS)
def test_each_mapping_is_read_by_one_items(member: str) -> None:
    counted = _Counted(getattr(LIBRARY, member))
    PackRegistry([replace(LIBRARY, **{member: counted}), ARCHIVE], core_version=CORE)
    assert counted.reads == ["items"]


@pytest.mark.parametrize("member", ["concepts", "analyses"])
def test_each_sequence_is_read_by_one_iteration(member: str) -> None:
    counted = _CountedSequence(getattr(LIBRARY, member))
    PackRegistry([replace(LIBRARY, **{member: counted}), ARCHIVE], core_version=CORE)
    assert counted.reads == ["__iter__"]


# --- The manifest and the concepts --------------------------------------------------------------


class _Raising(PackManifest):
    def __getattribute__(self, name: str) -> Any:
        if name in ("id", "version", "results_version", "requires_core"):
            raise ValueError(SECRET)
        return super().__getattribute__(name)


class _Formatting(str):
    def __format__(self, spec: str) -> str:
        raise ValueError(SECRET)

    def __str__(self) -> str:
        raise ValueError(SECRET)


def test_a_manifest_that_cannot_be_read_names_the_pack_by_its_position() -> None:
    raising = _Raising.model_validate(manifest().model_dump())
    found = _problems(ARCHIVE, replace(LIBRARY, manifest=raising))
    assert "the 2nd pack given: its manifest could not be read" in found
    _secretless(found)


def test_a_manifest_of_other_types_names_its_field() -> None:
    built = PackManifest.model_construct(
        id="library", version=_Formatting("1.2.0"), results_version=1, requires_core=">=0.1"
    )
    found = _problems(replace(LIBRARY, manifest=built))
    assert "the 1st pack given: its manifest's version is not of its type" in found
    _secretless(found)


def test_a_pack_named_by_its_position_still_has_its_other_problems_reported() -> None:
    raising = _Raising.model_validate(manifest().model_dump())
    found = _problems(
        replace(LIBRARY, manifest=raising, extension_schemas={"dataset": {"pattern": "^a"}})
    )
    assert any(p.startswith("the 1st pack given: the extension schema for dataset") for p in found)


class _Copying(ConceptDescriptor):
    def model_copy(self, *given: Any, **options: Any) -> Any:
        raise ValueError(SECRET)


def test_a_concept_is_read_back_as_an_exact_concept() -> None:
    sub = _Copying.model_validate(concept("library:loan_status").model_dump())
    packs = PackRegistry([replace(LIBRARY, concepts=(sub,)), ARCHIVE], core_version=CORE)
    assert [type(c) for c in packs.concepts()] == [ConceptDescriptor]
    assert packs.concepts() == [concept("library:loan_status")]


class _Dumping(ConceptDescriptor):
    dumped: ClassVar[list[str]] = []

    def model_dump_json(self, *given: Any, **options: Any) -> str:
        _Dumping.dumped.append("model_dump_json")
        return concept("library:other").model_dump_json()

    def model_dump(self, *given: Any, **options: Any) -> Any:
        _Dumping.dumped.append("model_dump")
        return concept("library:other").model_dump()


def test_a_concept_is_dumped_by_the_core_never_by_its_own_methods() -> None:
    _Dumping.dumped.clear()
    sub = _Dumping.model_validate(concept("library:loan_status").model_dump())
    packs = PackRegistry([replace(LIBRARY, concepts=(sub,)), ARCHIVE], core_version=CORE)
    assert packs.concepts() == [concept("library:loan_status")]
    assert _Dumping.dumped == []


class S3cr3tPackType:
    pass


def test_a_schema_value_that_is_not_json_is_named_without_its_type() -> None:
    schemas = {"dataset": {"type": "object", "default": S3cr3tPackType()}}
    found = _problems(replace(LIBRARY, extension_schemas=schemas))
    assert any("a value that is not JSON" in p for p in found)
    assert all("S3cr3t" not in p for p in found)


def test_a_concept_that_cannot_be_read_back_names_its_position() -> None:
    found = _problems(replace(LIBRARY, concepts=(concept("library:a"), object())))
    assert "library: its 2nd concept could not be read" in found


# --- Each member's read raising ------------------------------------------------------------------


class _Metaclass(type):
    def __hash__(cls) -> int:
        raise RuntimeError(SECRET)

    def __eq__(cls, other: object) -> bool:
        return other is MemoryError


_Said = _Metaclass("Said", (Exception,), {})


class _Stop(BaseException):
    pass


class _Interrupt(KeyboardInterrupt):
    pass


def _raisers() -> list[Callable[[], BaseException]]:
    return [
        lambda: ValueError(SECRET),
        lambda: PackError([SECRET]),
        lambda: _Stop(SECRET),
        lambda: _Interrupt(SECRET),
        lambda: _Said(SECRET),
    ]


class _RaisingMapping(Mapping[str, Any]):
    def __init__(self, raised: BaseException) -> None:
        self.raised = raised

    def __getitem__(self, key: str) -> Any:
        raise self.raised

    def __iter__(self) -> Iterator[str]:
        raise self.raised

    def __len__(self) -> int:
        raise self.raised

    def items(self) -> Any:
        raise self.raised


class _RaisingSequence(Sequence[Any]):
    def __init__(self, raised: BaseException) -> None:
        self.raised = raised

    def __getitem__(self, index: Any) -> Any:
        raise self.raised

    def __len__(self) -> int:
        raise self.raised

    def __iter__(self) -> Iterator[Any]:
        raise self.raised


class _EntryRaising(LoanRates):
    def __init__(self, raised: BaseException) -> None:
        self.raised = raised

    @property
    def entry(self) -> AnalysisDescriptor:
        raise self.raised


class _SchemaRaising(Overdue):
    def __init__(self, raised: BaseException) -> None:
        self.raised = raised

    @property
    def schema(self) -> Any:
        raise self.raised


def _member_raising(member: str, raised: BaseException) -> dict[str, Any]:
    if member in _MAPPINGS:
        return {member: _RaisingMapping(raised)}
    if member in ("concepts", "analyses"):
        return {member: _RaisingSequence(raised)}
    if member == "entry":
        return {"analyses": (_EntryRaising(raised),)}
    assert member == "schema"
    return {"leaf_kinds": {"library.overdue": _SchemaRaising(raised)}}


_MEMBERS = [*_MAPPINGS, "concepts", "analyses", "entry", "schema"]


@pytest.mark.parametrize("member", _MEMBERS)
@pytest.mark.parametrize("at", range(5), ids=["value", "PackError", "base", "interrupt sub", "eq"])
def test_a_member_whose_read_raises_is_a_problem_and_the_others_are_still_reported(
    member: str, at: int
) -> None:
    raised = _raisers()[at]()
    later = manifest(requires_core=">=9")
    given = replace(LIBRARY, manifest=later, **_member_raising(member, raised))
    found = _problems(given)
    assert any("could not be read" in p for p in found), found
    assert any("requires core" in p for p in found), found
    _secretless(found)


@pytest.mark.parametrize("passing", [MemoryError, KeyboardInterrupt, SystemExit])
def test_the_passed_types_stop_registration_as_new_instances(passing: type[BaseException]) -> None:
    later = _Counted({"library.X": Severity.INFO})
    given = replace(LIBRARY, concepts=_RaisingSequence(passing(SECRET)), caveat_codes=later)
    with pytest.raises(passing) as raised:
        PackRegistry([given, ARCHIVE], core_version=CORE)
    assert type(raised.value) is passing
    assert SECRET not in str(raised.value)
    assert raised.value.__context__ is None
    assert later.reads == []
    if passing is SystemExit:
        assert cast(SystemExit, raised.value).code == 1


# --- Names, wordings, codes ----------------------------------------------------------------------


class _Lying(str):
    __slots__ = ()

    def __hash__(self) -> int:
        raise RuntimeError(SECRET)

    def __eq__(self, other: object) -> bool:
        raise RuntimeError(SECRET)


@pytest.mark.parametrize("key", [CaveatCode.SMALL_N, "SMALL_N"], ids=["member", "str"])
def test_a_wording_is_keyed_by_a_core_code_or_its_name(key: Any) -> None:
    packs = PackRegistry([replace(LIBRARY, wording={key: "Few loans"}), ARCHIVE], core_version=CORE)
    assert packs.wordings(CaveatCode.SMALL_N, ["library"]) == [("library", "Few loans")]
    kept = packs.pack("library").wording
    assert [type(k) for k in kept] == [CaveatCode]


def test_a_wording_given_twice_or_by_a_lying_key_is_a_problem() -> None:
    twice = _Counted({})
    twice.given = cast(Any, {CaveatCode.SMALL_N: "a"})
    pairs = [(CaveatCode.SMALL_N, "a"), ("SMALL_N", "b"), (_Lying("SMALL_N"), "c")]

    class Pairs(_Counted):
        def items(self) -> Any:
            return pairs

    found = _problems(replace(LIBRARY, wording=Pairs({})))
    assert "library: the wording for SMALL_N is given twice" in found
    assert any("(a key that is not text), which is not a core caveat code" in p for p in found)
    _secretless(found)


def test_a_severity_is_exactly_a_severity() -> None:
    found = _problems(replace(LIBRARY, caveat_codes={"library.RENEWALS_ESTIMATED": "warn"}))
    assert "library: caveat code library.RENEWALS_ESTIMATED has no severity" in found


def test_names_are_quoted_escaped_and_cut() -> None:
    name = "\x1b]0;owned\x07\x1b[2J‮X"
    rival = replace(ARCHIVE, ontology_systems={name: lambda code: True})
    found = _problems(replace(LIBRARY, ontology_systems={name: lambda code: True}), rival)
    [claimed] = [p for p in found if "is registered by" in p]
    assert "\x1b" not in claimed
    assert "\\u001b]0;owned\\u0007\\u001b[2J\\u202eX" in claimed
    long = "x" * 300
    found = _problems(replace(LIBRARY, leaf_kinds={long: Overdue()}))
    [named] = [p for p in found if "leaf kind" in p]
    assert "x" * 200 + "…" in named
    assert "x" * 201 not in named


# --- Nothing read after registration -------------------------------------------------------------


class _Watched:
    """A hook object that records every attribute read and every operation on it."""

    reads: list[str]

    def __init__(self) -> None:
        object.__setattr__(self, "reads", [])

    def __getattribute__(self, name: str) -> Any:
        if name not in ("reads", "__class__", "__dict__"):
            object.__getattribute__(self, "reads").append(name)
        return object.__getattribute__(self, name)

    def __call__(self, *given: Any) -> Any:
        object.__getattribute__(self, "reads").append("__call__")
        return True

    def __bool__(self) -> bool:
        object.__getattribute__(self, "reads").append("__bool__")
        return True

    def __eq__(self, other: object) -> bool:
        object.__getattribute__(self, "reads").append("__eq__")
        return self is other

    def __hash__(self) -> int:
        object.__getattribute__(self, "reads").append("__hash__")
        return id(self)

    def __getattr__(self, name: str) -> Any:
        object.__getattribute__(self, "reads").append(f"__getattr__ {name}")
        raise AttributeError(name)

    def __len__(self) -> int:
        object.__getattribute__(self, "reads").append("__len__")
        return 1

    def __repr__(self) -> str:
        object.__getattribute__(self, "reads").append("__repr__")
        return "watched"

    def __format__(self, spec: str) -> str:
        object.__getattribute__(self, "reads").append("__format__")
        return "watched"


class _WatchedLeaf(_Watched):
    @property
    def schema(self) -> Any:
        return {"type": "object"}


class _WatchedAnalysis(_Watched):
    @property
    def entry(self) -> AnalysisDescriptor:
        return entry("library.loan_rates")


def _watched_pack() -> tuple[Pack, list[_Watched]]:
    hooks = {
        name: _Watched()
        for name in (
            "importer",
            "validator",
            "proposer",
            "facet",
            "caveat_rule",
            "system",
            "translator",
            "predicate",
        )
    }
    leaf, analysis = _WatchedLeaf(), _WatchedAnalysis()
    pack = replace(
        LIBRARY,
        importer=hooks["importer"],
        validator=hooks["validator"],
        proposer=hooks["proposer"],
        facet=hooks["facet"],
        caveat_rule=hooks["caveat_rule"],
        ontology_systems={"LIBRARY-CODES": hooks["system"]},
        translators={"library.marc": hooks["translator"]},
        requirement_predicates={"has_loans": hooks["predicate"]},
        leaf_kinds={"library.overdue": leaf},
        analyses=(analysis,),
    )
    return cast(Pack, pack), [*hooks.values(), leaf, analysis]


def test_no_hook_object_is_read_after_registration() -> None:
    pack, hooks = _watched_pack()
    packs = PackRegistry([pack, ARCHIVE], core_version=CORE)
    for hook in hooks:
        object.__getattribute__(hook, "reads").clear()
    handed = [
        packs.pack("library"),
        *packs.listed(["library", "archive"]),
        packs.importer("library"),
        packs.leaf_kind("library.overdue"),
        packs.translator("library.marc"),
        packs.analysis("library.loan_rates"),
        packs.requirement_predicate("library.has_loans"),
        packs.ontology_validator("LIBRARY-CODES"),
        *packs.validators(["library"]),
        *packs.proposers(["library"]),
        *packs.facets(["library"]),
        *packs.caveat_rules(["library"]),
        packs.analyses(),
        packs.concepts(),
        packs.extension_schemas("dataset", ["library"]),
        packs.wordings(CaveatCode.SMALL_N, ["library"]),
        packs.severity("library.RENEWALS_ESTIMATED"),
        packs.leaf_kinds(),
        packs.leaf_checker("library.overdue"),
        packs.analysis_checkers("library.loan_rates"),
        packs.ids,
    ]
    assert handed
    called = {
        "pack", "listed", "importer", "leaf_kind", "leaf_summary", "translator", "analysis",
        "requirement_predicate", "ontology_validator", "validators", "proposers", "facets",
        "caveat_rules", "analyses", "concepts", "extension_schemas", "wordings", "severity",
        "leaf_kinds", "leaf_checker", "analysis_checkers", "ids",
    }  # fmt: skip
    public = {name for name, _ in inspect.getmembers(PackRegistry) if not name.startswith("_")}
    assert public == called, "a registry method this test does not call"
    for given in [packs.pack("library"), *packs.listed(["library", "archive"])]:
        assert given is not None
        for field in dataclasses.fields(given):
            getattr(given, field.name)
        given.id  # noqa: B018 - the property reads the manifest
    handles = [
        packs.importer("library"),
        packs.leaf_kind("library.overdue"),
        packs.leaf_summary("library.overdue"),
        packs.translator("library.marc"),
        packs.requirement_predicate("library.has_loans"),
        packs.ontology_validator("LIBRARY-CODES"),
        *packs.validators(["library"]),
        *packs.proposers(["library"]),
        *packs.facets(["library"]),
        *packs.caveat_rules(["library"]),
    ]
    found = packs.analysis("library.loan_rates")
    assert found is not None
    handles.append(found.implementation)
    for handle in handles:
        assert type(handle) is Hook
        repr(handle)
        hash(handle)
        assert handle == handle
        handle.pack  # noqa: B018 - the property reads the handle's own text
        handle.stage  # noqa: B018
    assert [object.__getattribute__(h, "reads") for h in hooks] == [[] for _ in hooks]
    assert _held(packs.importer("library")) is pack.importer
    assert _held(packs.leaf_kind("library.overdue")) is pack.leaf_kinds["library.overdue"]
    assert _held(found.implementation) is pack.analyses[0]


def _held(handle: Hook[Any] | None) -> object:
    """The hook object a handle holds, read from its slot as only a test does."""
    assert handle is not None
    return object.__getattribute__(handle, "_hook_object")


def test_the_registry_hands_out_handles_on_the_pack_s_own_hook_objects() -> None:
    packs = PackRegistry([LIBRARY, ARCHIVE], core_version=CORE)
    assert isinstance(_held(packs.leaf_kind("library.overdue")), Overdue)
    assert isinstance(_held(packs.translator("library.marc")), Marc)
    found = packs.analysis("library.loan_rates")
    assert type(found) is RegisteredAnalysis
    assert _held(found.implementation) is LIBRARY.analyses[0]
    assert packs.leaf_kind("library.overdue") is packs.leaf_kind("library.overdue")
    assert found.implementation is packs.analyses()[0].implementation


# --- What the registry keeps ----------------------------------------------------------------------

_LEAVES: tuple[type, ...] = (str, int, float, bool, type(None), CaveatCode, Severity)


def _walk(value: object, hooks: list[object], seen: set[int]) -> None:
    """Every object the registry keeps is a core-made value of an allowed type, or a handle on
    one of the pack's hook objects, by identity; no hook object is kept but in a handle."""
    if type(value) is Hook:
        assert any(_held(cast(Hook[Any], value)) is hook for hook in hooks)
        return
    kind = type(value)
    if kind is CaveatCode or kind is Severity:
        enum = cast(Any, kind)
        assert any(value is member for member in enum), "a fake enum member is kept"
        return
    if any(kind is leaf for leaf in _LEAVES) or kind is Checker:
        return
    if id(value) in seen:
        return
    seen.add(id(value))
    if kind is dict or kind is MappingProxyType:
        for key, member in cast(Mapping[object, object], value).items():
            _walk(key, hooks, seen)
            _walk(member, hooks, seen)
        return
    if kind is list or kind is tuple:
        for item in cast(Sequence[object], value):
            _walk(item, hooks, seen)
        return
    if isinstance(value, BaseModel):
        assert kind.__module__.startswith("aibi.core."), kind
        for name in type(value).model_fields:
            _walk(getattr(value, name), hooks, seen)
        _walk(value.model_extra, hooks, seen)
        return
    if kind is PackInfo or kind is RegisteredAnalysis or kind is pack_api._Hooks:  # pyright: ignore[reportPrivateUsage]
        for field in dataclasses.fields(cast(Any, value)):
            _walk(getattr(value, field.name), hooks, seen)
        return
    raise AssertionError(f"the registry keeps a {kind.__name__}")


def test_the_registry_keeps_only_core_made_values_and_the_hook_objects() -> None:
    pack, hooks = _watched_pack()
    packs = PackRegistry([pack, ARCHIVE], core_version=CORE)
    for name, kept in vars(packs).items():
        assert name.startswith("_")
        _walk(kept, cast(list[object], hooks), set())


# --- Round 1 of #78: every read guarded, only the core's own reasons quoted ----------------------


class _EqRaising(str):
    """A key that hashes as its text and raises when compared, so that a lookup of the attribute
    it names runs the pack's code."""

    __slots__ = ()

    def __hash__(self) -> int:
        return str.__hash__(self)

    def __eq__(self, other: object) -> bool:
        raise RuntimeError(f"\x1b[31m{SECRET}")


class _PassingEq(str):
    """A key whose comparison raises the registry's own stop, naming a pack's type."""

    __slots__ = ()

    def __hash__(self) -> int:
        return str.__hash__(self)

    def __eq__(self, other: object) -> bool:
        raise pack_api._Passing(_Stop)  # pyright: ignore[reportPrivateUsage]


_HOOKS = {
    "importer": "importer",
    "validator": "validator",
    "proposer": "proposer",
    "facet": "facet",
    "caveat_rule": "caveat rule",
}


@pytest.mark.parametrize("key", [_EqRaising, _PassingEq], ids=["raising", "passing"])
@pytest.mark.parametrize("hook", list(_HOOKS))
def test_a_hook_field_is_read_inside_its_guard(hook: str, key: type[str]) -> None:
    given = replace(LIBRARY, manifest=manifest(requires_core=">=9"))
    fields = vars(given)
    held = fields.pop(hook)
    fields[key(hook)] = held
    found = _problems(given, ARCHIVE)
    assert f"library: its {_HOOKS[hook]} could not be read" in found, found
    assert any("requires core" in p for p in found), found
    _secretless(found)


def test_only_a_stop_a_guard_raised_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    def stopping(*args: object) -> Any:
        raise pack_api._Passing(MemoryError)  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(pack_api, "_registered", stopping)
    found = _problems(LIBRARY, ARCHIVE)
    assert found == ["the 1st pack given: registration was stopped by what is not a guard's"]
    with pytest.raises(PackError) as raised:
        PackRegistry([ARCHIVE, LIBRARY], core_version=CORE, labels=["shelves.a", "shelves.b"])
    assert raised.value.problems == (
        "the module shelves.a: registration was stopped by what is not a guard's",
    )


class _NotJsonRaising(Mapping[str, Any]):
    """A mapping whose read raises the core's own refusal of a value, with the pack's text."""

    def __getitem__(self, key: str) -> Any:
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def items(self) -> Any:
        raise pack_api._NotJsonError(f"\x1b[31m{SECRET}")  # pyright: ignore[reportPrivateUsage]


class _NotJsonSchema(Overdue):
    @property
    def schema(self) -> Any:
        return _NotJsonRaising()


class _NotJsonEntry(LoanRates):
    @property
    def entry(self) -> AnalysisDescriptor:
        raise pack_api._NotJsonError(f"\x1b[31m{SECRET}")  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("given", "said"),
    [
        (
            {"extension_schemas": {"dataset": _NotJsonRaising()}},
            "library: its extension schema for dataset could not be read",
        ),
        (
            {"leaf_kinds": {"library.overdue": _NotJsonSchema()}},
            "library: its leaf kind library.overdue's schema could not be read",
        ),
        (
            {"analyses": (_NotJsonEntry(),)},
            "library: its 1st analysis's entry could not be read",
        ),
    ],
    ids=["extension", "leaf", "entry"],
)
def test_a_refusal_the_pack_raised_is_never_quoted(given: dict[str, Any], said: str) -> None:
    found = _problems(replace(LIBRARY, **given), ARCHIVE)
    assert said in found, found
    _secretless(found)
    assert all("\x1b" not in p for p in found), found


class _Key(str):
    """A key equal only to itself, so that two of the same text are two keys of a dict."""

    __slots__ = ()

    def __hash__(self) -> int:
        return object.__hash__(self)

    def __eq__(self, other: object) -> bool:
        return self is other


def _entry_with(change: Callable[[AnalysisDescriptor], None]) -> AnalysisDescriptor:
    found = entry("library.loan_rates")
    change(found)
    return found


def _twice(schema: dict[Any, Any]) -> None:
    schema.clear()
    schema[_Key("type")] = "object"
    schema[_Key("type")] = "array"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda e: e.fields.params.update(default=float("nan")), "a number that is not finite"),
        (lambda e: e.fields.returns.update(default=float("inf")), "a number that is not finite"),
        (lambda e: _twice(e.fields.params), "two keys written as the same text"),
        (lambda e: _twice(e.fields.returns), "two keys written as the same text"),
    ],
    ids=["params nan", "returns inf", "params twice", "returns twice"],
)
def test_an_entry_that_json_would_not_carry_unchanged_is_refused(
    change: Callable[[AnalysisDescriptor], None], reason: str
) -> None:
    given = replace(LIBRARY, analyses=(_Fixed(_entry_with(change)),))
    found = _problems(given, ARCHIVE)
    assert f"library: its 1st analysis's entry is not a JSON value: it holds {reason}" in found


def test_a_concept_that_json_would_not_carry_unchanged_is_refused() -> None:
    def extended(id: str) -> ConceptDescriptor:
        given = concept(id).model_dump(mode="json", exclude_unset=True)
        return ConceptDescriptor.model_validate({**given, "extensions": {"library": {"y": 0}}})

    twice = extended("library:status")
    extension = cast(dict[Any, Any], twice.extensions["library"])
    extension.clear()
    extension[_Key("x")] = 1
    extension[_Key("x")] = 2
    nan = extended("library:other")
    cast(dict[Any, Any], nan.extensions["library"])["y"] = float("nan")
    found = _problems(replace(LIBRARY, concepts=(twice, nan)), ARCHIVE)
    assert (
        "library: its 1st concept is not a JSON value: it holds two keys written as the same text"
        in found
    )
    assert "library: its 2nd concept is not a JSON value: it holds a number that is not finite" in (
        found
    )


_THROUGH_A_SCALAR: dict[str, Any] = {"foo": 5, "$ref": "#/foo/x"}


class _Schema(Overdue):
    def __init__(self, schema: Mapping[str, Any]) -> None:
        self._schema = schema

    @property
    def schema(self) -> Any:
        return self._schema


@pytest.mark.parametrize(
    "given",
    [
        lambda: {"extension_schemas": {"dataset": dict(_THROUGH_A_SCALAR)}},
        lambda: {"leaf_kinds": {"library.overdue": _Schema(dict(_THROUGH_A_SCALAR))}},
        lambda: {
            "analyses": (_Fixed(_entry_with(lambda e: e.fields.params.update(_THROUGH_A_SCALAR))),)
        },
        lambda: {
            "analyses": (_Fixed(_entry_with(lambda e: e.fields.returns.update(_THROUGH_A_SCALAR))),)
        },
    ],
    ids=["extension", "leaf", "params", "returns"],
)
def test_a_reference_through_a_scalar_resolves_to_nothing(given: Callable[[], Any]) -> None:
    found = _problems(replace(LIBRARY, manifest=manifest(requires_core=">=9"), **given()), ARCHIVE)
    assert any("$ref that resolves to nothing" in p for p in found), found
    assert any("requires core" in p for p in found), found


def test_the_core_s_own_failure_in_a_schema_s_check_is_never_the_pack_s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing(schema: object) -> list[str]:
        raise RuntimeError("the core's own")

    monkeypatch.setattr(pack_api, "schema_problems", failing)
    with pytest.raises(RuntimeError, match="the core's own"):
        PackRegistry([LIBRARY, ARCHIVE], core_version=CORE)


def test_the_handed_out_pack_holds_no_hook() -> None:
    pack, _ = _watched_pack()
    packs = PackRegistry([pack, ARCHIVE], core_version=CORE)
    handed = packs.pack("library")
    assert handed.analyses == ("library.loan_rates",)
    _walk(handed, [], set())


def test_repr_and_equality_read_no_hook() -> None:
    pack, hooks = _watched_pack()
    packs = PackRegistry([pack, ARCHIVE], core_version=CORE)
    for hook in hooks:
        object.__getattribute__(hook, "reads").clear()
    handed = packs.pack("library")
    repr(handed)
    assert handed == packs.pack("library")
    found = packs.analysis("library.loan_rates")
    assert found is not None
    repr(found)
    assert found == packs.analysis("library.loan_rates")
    assert [object.__getattribute__(h, "reads") for h in hooks] == [[] for _ in hooks]


class _Pairs(Mapping[Any, Any]):
    """A mapping whose ``items`` gives ``pairs`` as they are, keys that a dict could not hold
    twice, or at all, included."""

    def __init__(self, pairs: list[tuple[Any, Any]]) -> None:
        self.pairs = pairs

    def __getitem__(self, key: Any) -> Any:
        raise KeyError(key)

    def __iter__(self) -> Iterator[Any]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def items(self) -> Any:
        return self.pairs


_GIVEN_TWICE = [
    ("extension_schemas", "dataset", {"type": "object"}, "extension schema for dataset"),
    ("leaf_kinds", "library.overdue", Overdue(), "leaf kind library.overdue"),
    ("translators", "library.marc", Marc(), "translator format library.marc"),
    ("ontology_systems", "LIBRARY-CODES", lambda code: True, "ontology system LIBRARY-CODES"),
    ("requirement_predicates", "has_loans", lambda view: True, "requirement predicate has_loans"),
    ("caveat_codes", "library.RENEWALS_ESTIMATED", Severity.WARN, "caveat code library.RENEWAL"),
]


@pytest.mark.parametrize(("member", "key", "value", "said"), _GIVEN_TWICE)
def test_a_key_given_twice_is_a_problem(member: str, key: str, value: Any, said: str) -> None:
    given = replace(LIBRARY, **{member: _Pairs([(key, value), (key, value)])})
    found = _problems(given, ARCHIVE)
    assert any(p.startswith(f"library: {said}") and "is given twice" in p for p in found), found


@pytest.mark.parametrize(("member", "key", "value", "said"), _GIVEN_TWICE)
def test_a_key_that_is_not_exactly_a_str_is_a_problem(
    member: str, key: str, value: Any, said: str
) -> None:
    given = replace(LIBRARY, **{member: _Pairs([(_Lying(key), value)])})
    found = _problems(given, ARCHIVE)
    assert any("(a key that is not text)" in p or "not Unicode text" in p for p in found), found
    _secretless(found)


def test_an_extension_schema_whose_read_raises_is_its_problem() -> None:
    given = replace(LIBRARY, extension_schemas={"dataset": _RaisingMapping(ValueError(SECRET))})
    found = _problems(given, ARCHIVE)
    assert "library: its extension schema for dataset could not be read" in found
    _secretless(found)


def test_a_pack_named_by_its_position_has_no_namespace_problem() -> None:
    given = replace(LIBRARY, manifest=_Raising.model_construct())
    found = _problems(given, ARCHIVE)
    assert all("is not" not in p or "manifest" in p for p in found), found
    bad = replace(given, leaf_kinds=_Pairs([(1, Overdue())]), caveat_codes=_Pairs([(1, 1)]))
    found = _problems(bad, ARCHIVE)
    assert "the 1st pack given: leaf kind (a key that is not text) is not <pack id>.<name>" in found
    assert "the 1st pack given: caveat code (a key that is not text) is not <pack id>.<CODE>" in (
        found
    )


def test_a_manifest_that_is_not_valid_quotes_no_value() -> None:
    given = replace(
        LIBRARY,
        manifest=PackManifest.model_construct(
            id="library", version=f"\x1b{SECRET}", results_version=1, requires_core=">=0"
        ),
    )
    found = _problems(given, ARCHIVE)
    assert "the 1st pack given: its manifest's version is not valid" in found, found
    _secretless(found)


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("a\x7fb", "a\\u007fb"),
        ("a\\u001b", "a\\\\u001b"),
        ("x" * 200, "x" * 200),
        ("x" * 201, "x" * 200 + "…"),
        ("a\U0001f600", "a\\U0001f600"),
    ],
    ids=["del", "backslash", "exactly 200", "201", "astral"],
)
def test_a_name_is_escaped_and_cut_exactly(name: str, shown: str) -> None:
    found = _problems(replace(LIBRARY, leaf_kinds={name: Overdue()}), ARCHIVE)
    assert f"library: leaf kind {shown} is not library.<name>" in found, found


def test_a_schema_pointer_is_escaped() -> None:
    schema = {"properties": {"\x1b[31m": {"type": 5}}}
    found = _problems(replace(LIBRARY, extension_schemas={"dataset": schema}), ARCHIVE)
    [refused] = [p for p in found if "is refused" in p]
    assert "\x1b" not in refused
    assert "\\u001b[31m" in refused


# --- Round 2 of #78: enum members by identity, the core's own copies outside the guards ----------


def _fake[E](kind: type[E], text: str, value: object) -> E:
    """An object of the enum's exact type that is no member of it."""
    fake = str.__new__(cast(Any, kind), text)
    object.__setattr__(fake, "_value_", value)
    return cast(E, fake)


def test_a_wording_keyed_by_a_fake_member_is_a_problem() -> None:
    fake = _fake(CaveatCode, "SMALL_N", f"\x1b[31m{SECRET}")
    found = _problems(replace(LIBRARY, wording={fake: 42}), ARCHIVE)
    assert any("(a key that is not text), which is not a core caveat code" in p for p in found)
    _secretless(found)
    assert all("\x1b" not in p for p in found), found
    bare = str.__new__(cast(Any, CaveatCode), "SMALL_N")
    found = _problems(replace(LIBRARY, wording={bare: "Few loans"}), ARCHIVE)
    assert any("which is not a core caveat code" in p for p in found), found


def test_a_fake_severity_is_no_severity() -> None:
    fake = _fake(Severity, "info", "block")
    found = _problems(replace(LIBRARY, caveat_codes={"library.RENEWALS_ESTIMATED": fake}))
    assert "library: caveat code library.RENEWALS_ESTIMATED has no severity" in found
    packs = PackRegistry([LIBRARY, ARCHIVE], core_version=CORE)
    kept = packs.severity("library.RENEWALS_ESTIMATED")
    assert any(kept is member for member in Severity)


def test_the_core_s_own_copy_of_an_entry_s_schema_fails_as_the_core_s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = pack_api._plain  # pyright: ignore[reportPrivateUsage]

    def failing(value: object, *given: Any) -> Any:
        if isinstance(value, dict) and value.get("marker") == "params":
            raise RuntimeError("the core's own")
        return real(value, *given)

    marked = _entry_with(lambda e: e.fields.params.update(marker="params"))
    monkeypatch.setattr(pack_api, "_plain", failing)
    with pytest.raises(RuntimeError, match="the core's own"):
        PackRegistry([replace(LIBRARY, analyses=(_Fixed(marked),)), ARCHIVE], core_version=CORE)


def test_keys_written_twice_inside_an_array_are_refused() -> None:
    def twice(e: AnalysisDescriptor) -> None:
        inner: dict[Any, Any] = {}
        inner[_Key("type")] = "object"
        inner[_Key("type")] = "array"
        e.fields.params["allOf"] = [inner]

    found = _problems(replace(LIBRARY, analyses=(_Fixed(_entry_with(twice)),)), ARCHIVE)
    assert (
        "library: its 1st analysis's entry is not a JSON value: it holds two keys written as the "
        "same text" in found
    )


def test_equality_and_repr_of_distinct_hooks_read_neither() -> None:
    one, other = _Watched(), _Watched()
    first = Pack(manifest=LIBRARY.manifest, importer=cast(Any, one), facet=cast(Any, one))
    second = Pack(manifest=LIBRARY.manifest, importer=cast(Any, other), facet=cast(Any, other))
    repr(first)
    assert first == second
    entry_ = entry("library.loan_rates")
    a = RegisteredAnalysis(entry_, cast(Any, one))
    b = RegisteredAnalysis(entry_, cast(Any, other))
    repr(a)
    assert a == b
    assert object.__getattribute__(one, "reads") == []
    assert object.__getattribute__(other, "reads") == []


def test_a_handed_out_analysis_changes_nothing_registered() -> None:
    packs = PackRegistry([LIBRARY, ARCHIVE], core_version=CORE)
    found = packs.analysis("library.loan_rates")
    assert found is not None
    found._entry.fields.params["evil"] = 1  # pyright: ignore[reportPrivateUsage]
    [listed] = [a for a in packs.analyses() if a.entry.id == "library.loan_rates"]
    listed._entry.fields.params["worse"] = 1  # pyright: ignore[reportPrivateUsage]
    again = packs.analysis("library.loan_rates")
    assert again is not None
    assert "evil" not in again.entry.fields.params
    assert "worse" not in again.entry.fields.params


# --- Round 3 of #78: the two fixes of round 2 that had no test ------------------------------------


def test_the_manifest_is_read_once() -> None:
    """The registry reads the given pack's ``manifest`` once, its fields from that one object.
    This is a property of the code, checked on its syntax: counting a key's comparisons in the
    instance dictionary depends on CPython's specializing interpreter, which may answer a read
    from a cached index without a comparison, or compare once more when it specializes."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(pack_api._manifest)))  # pyright: ignore[reportPrivateUsage]
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "manifest"
        and isinstance(node.value, ast.Name)
        and node.value.id == "given"
    ]
    assert len(reads) == 1


def test_a_schema_s_problem_is_cut_with_its_pointer() -> None:
    schema = {"properties": {"x" * 1000: {"type": 5}}}
    found = _problems(replace(LIBRARY, extension_schemas={"dataset": schema}), ARCHIVE)
    [refused] = [p for p in found if "is refused" in p]
    assert "x" * 900 not in refused
    assert refused.endswith("…")

"""A pack's registration allowance and the registry's counts (SPEC §10.1, D402, D421).

Each pack's registration copies what it gave within an allowance of its own,
``MAX_PACK_VALUES`` JSON values and ``MAX_PACK_CHARACTERS`` characters of text, keys included,
charged at the sites ``pack_api.CHARGED`` names: its concepts and its analyses' entries (their
dumps, read back) and its extension and leaf kinds' schemas (copied); never the re-copy of an
entry's ``params`` and ``returns`` (``_kept_schema``). Passing it is a ``PackError`` naming the
limit, once for the pack. The registry counts every pack's analyses and leaf kinds together
(``MAX_PACK_ANALYSES``, ``MAX_LEAF_KINDS``), naming each pack's share in pack id order.

Every boundary is tested exactly, at the limit and one over, with sizes computed by ``charged``,
an oracle written from the definition (each object, array, string, number, boolean and null is a
value; each key's and string's characters count), not by the code under test. No domain: the
packs are a garden's, an orchard's and a weather station's.
"""

from collections.abc import Callable, Iterator, Mapping
from typing import Any, ClassVar, cast

import pytest
from pydantic import JsonValue, TypeAdapter
from tests.core.schema.test_pack_api import CORE, manifest

from aibi.core.api import packs as loader
from aibi.core.schema import pack_api
from aibi.core.schema.caveats import CaveatCode, Severity
from aibi.core.schema.descriptors import RELEASE_KINDS, AnalysisDescriptor, ConceptDescriptor
from aibi.core.schema.guards import JsonTooLarge
from aibi.core.schema.limits import (
    MAX_LEAF_KINDS,
    MAX_PACK_ANALYSES,
    MAX_PACK_CHARACTERS,
    MAX_PACK_CONCEPTS,
    MAX_PACK_VALUES,
)
from aibi.core.schema.pack_api import Pack, PackError, PackRegistry

VALUES_PROBLEM = (
    "more than 500000 JSON values of its schemas, concepts and analyses' entries together "
    "(pack_values)"
)
CHARACTERS_PROBLEM = (
    "more than 8388608 characters of text of its schemas, concepts and analyses' entries "
    "together (pack_characters)"
)


def charged(value: object) -> tuple[int, int]:
    """The values and characters a copy of ``value`` is charged with, from the definition."""
    if isinstance(value, dict):
        found = [charged(member) for member in cast(dict[str, object], value).values()]
        keys = sum(len(key) for key in cast(dict[str, object], value))
        return 1 + sum(v for v, _ in found), keys + sum(c for _, c in found)
    if isinstance(value, list):
        found = [charged(item) for item in cast(list[object], value)]
        return 1 + sum(v for v, _ in found), sum(c for _, c in found)
    if isinstance(value, str):
        return 1, len(value)
    return 1, 0


def test_the_oracle_counts_by_the_definition() -> None:
    assert charged({"ab": [0, "xyz", None, True, 1.5, {}]}) == (8, 5)
    assert charged([]) == (1, 0)
    assert charged("") == (1, 0)


class _Leaf:
    def __init__(self, schema: object, reads: list[str] | None = None) -> None:
        self._schema = schema
        self.reads = reads

    @property
    def schema(self) -> Any:
        if self.reads is not None:
            self.reads.append("schema")
        return self._schema

    def compile(self, leaf: Any, release: Any, pack_version: str) -> list[Any]:
        return []

    def summary(self, leaf: Any) -> list[Any]:
        return []


class _Analysis:
    def __init__(self, entry: AnalysisDescriptor) -> None:
        self._entry = entry

    @property
    def entry(self) -> AnalysisDescriptor:
        return self._entry

    def run(self, inputs: Any) -> dict[str, JsonValue]:
        return {}


def _entry(id: str, params: dict[str, JsonValue] | None = None) -> AnalysisDescriptor:
    return AnalysisDescriptor.model_validate(
        {
            "kind": "analysis",
            "id": id,
            "version": "1.0.0",
            "label": "Growth",
            "fields": {
                "requires": [{"role": "cohorts"}],
                "params": params or {"type": "object"},
                "returns": {"type": "object"},
                "methods": {},
                "assumptions": [],
                "uses_reference": False,
                "assumes_independent_groups": True,
                "cross_dataset": None,
                "caveats": [],
            },
        }
    )


def _pack(pack: str = "garden", **members: Any) -> Pack:
    return Pack(manifest=manifest(id=pack, version="2.0.0", requires_core=">=0"), **members)


def _zeros(values: int) -> dict[str, JsonValue]:
    """A schema charged with exactly ``values`` values."""
    schema: dict[str, JsonValue] = {"type": "object", "enum": []}
    schema["enum"] = list[JsonValue]([0] * (values - charged(schema)[0]))
    assert charged(schema)[0] == values
    return schema


def _text(characters: int) -> dict[str, JsonValue]:
    """A schema charged with exactly ``characters`` characters."""
    schema: dict[str, JsonValue] = {"type": "object", "description": ""}
    schema["description"] = "a" * (characters - charged(schema)[1])
    assert charged(schema)[1] == characters
    return schema


def _problems(*given: Pack) -> list[str]:
    with pytest.raises(PackError) as raised:
        PackRegistry(given, core_version=CORE)
    return list(raised.value.problems)


# --- Each charged site, at its limit and one over --------------------------------------------


@pytest.mark.parametrize("over", [0, 1])
def test_an_extension_schema_is_charged_to_its_pack(over: int) -> None:
    pack = _pack(extension_schemas={"dataset": _zeros(MAX_PACK_VALUES + over)})
    if not over:
        assert PackRegistry([pack], core_version=CORE).ids == ("garden",)
        return
    assert _problems(pack) == [
        "garden: its extension schema for dataset is beyond what a pack's registration copies: "
        + VALUES_PROBLEM
    ]


@pytest.mark.parametrize("over", [0, 1])
def test_a_leaf_kind_s_schema_is_charged_to_its_pack(over: int) -> None:
    pack = _pack(leaf_kinds={"garden.wilted": _Leaf(_zeros(MAX_PACK_VALUES + over))})
    if not over:
        registry = PackRegistry([pack], core_version=CORE)
        assert registry.leaf_schemas() == {"garden.wilted": _zeros(MAX_PACK_VALUES)}
        return
    assert _problems(pack) == [
        "garden: its leaf kind garden.wilted's schema is beyond what a pack's registration "
        "copies: " + VALUES_PROBLEM
    ]


def _concept(values: int) -> ConceptDescriptor:
    """A concept whose dump is charged with exactly ``values`` values."""
    base = {"kind": "concept", "id": "garden:bloom", "version": 1, "label": "Bloom"}
    made = ConceptDescriptor.model_validate(
        {**base, "fields": {"sort": "value"}, "extensions": {"garden": {"x": []}}}
    )
    dumped = TypeAdapter(ConceptDescriptor).dump_python(made, mode="json")
    zeros = values - charged(dumped)[0]
    made = ConceptDescriptor.model_validate(
        {**base, "fields": {"sort": "value"}, "extensions": {"garden": {"x": [0] * zeros}}}
    )
    assert charged(TypeAdapter(ConceptDescriptor).dump_python(made, mode="json"))[0] == values
    return made


@pytest.mark.parametrize("over", [0, 1])
def test_a_concept_is_charged_to_its_pack(over: int) -> None:
    pack = _pack(concepts=(_concept(MAX_PACK_VALUES + over),))
    if not over:
        assert [c.id for c in PackRegistry([pack], core_version=CORE).concepts()] == [
            "garden:bloom"
        ]
        return
    assert _problems(pack) == [
        "garden: its 1st concept is beyond what a pack's registration copies: " + VALUES_PROBLEM
    ]


def _analysis(values: int) -> AnalysisDescriptor:
    """An entry whose dump is charged with exactly ``values`` values, its ``params`` most."""
    made = _entry("garden.growth", {"type": "object", "enum": []})
    dumped = TypeAdapter(AnalysisDescriptor).dump_python(made, mode="json")
    zeros = list[JsonValue]([0] * (values - charged(dumped)[0]))
    made = _entry("garden.growth", {"type": "object", "enum": zeros})
    assert charged(TypeAdapter(AnalysisDescriptor).dump_python(made, mode="json"))[0] == values
    return made


@pytest.mark.parametrize("over", [0, 1])
def test_an_analysis_s_entry_is_charged_to_its_pack(over: int) -> None:
    """At the limit the entry registers: its ``params`` and ``returns``, copied again by
    ``_kept_schema``, are charged to no allowance (were they charged to the pack's, the trip
    would be raised outside every guard, and no ``PackError``)."""
    pack = _pack(analyses=(_Analysis(_analysis(MAX_PACK_VALUES + over)),))
    if not over:
        assert PackRegistry([pack], core_version=CORE).analysis_versions() == [
            ("garden.growth", "1.0.0")
        ]
        return
    assert _problems(pack) == [
        "garden: its 1st analysis's entry is beyond what a pack's registration copies: "
        + VALUES_PROBLEM
    ]


@pytest.mark.parametrize("over", [0, 1])
def test_characters_are_charged_keys_included(over: int) -> None:
    pack = _pack(extension_schemas={"dataset": _text(MAX_PACK_CHARACTERS + over)})
    if not over:
        assert PackRegistry([pack], core_version=CORE).ids == ("garden",)
        return
    assert _problems(pack) == [
        "garden: its extension schema for dataset is beyond what a pack's registration copies: "
        + CHARACTERS_PROBLEM
    ]


@pytest.mark.parametrize("over", [0, 1])
def test_an_entry_s_characters_are_charged_keys_included(over: int) -> None:
    made = _entry("garden.growth", {"type": "object", "description": ""})
    dumped = TypeAdapter(AnalysisDescriptor).dump_python(made, mode="json")
    pad = "a" * (MAX_PACK_CHARACTERS + over - charged(dumped)[1])
    made = _entry("garden.growth", {"type": "object", "description": pad})
    dumped = TypeAdapter(AnalysisDescriptor).dump_python(made, mode="json")
    assert charged(dumped)[1] == MAX_PACK_CHARACTERS + over
    pack = _pack(analyses=(_Analysis(made),))
    if not over:
        assert PackRegistry([pack], core_version=CORE).analysis_versions() == [
            ("garden.growth", "1.0.0")
        ]
        return
    assert _problems(pack) == [
        "garden: its 1st analysis's entry is beyond what a pack's registration copies: "
        + CHARACTERS_PROBLEM
    ]


@pytest.mark.parametrize("site", ["concept", "leaf kind", "analysis"])
def test_every_charged_site_shares_the_pack_s_one_allowance(site: str) -> None:
    """The extension schema and one other site share the limit, one value over it together:
    the pack is refused where the two pass it (a concept is read before the schemas, so there
    the schema passes it), and at the limit together it registers."""
    if site == "concept":
        members: dict[str, Any] = {"concepts": (_concept(11),)}
        first = _zeros(MAX_PACK_VALUES - 10)
        where = "its extension schema for dataset"
    elif site == "leaf kind":
        members = {"leaf_kinds": {"garden.wilted": _Leaf(_zeros(11))}}
        first = _zeros(MAX_PACK_VALUES - 10)
        where = "its leaf kind garden.wilted's schema"
    else:
        members = {"analyses": (_Analysis(_analysis(11 + 50)),)}
        first = _zeros(MAX_PACK_VALUES - 60)
        where = "its 1st analysis's entry"
    pack = _pack(extension_schemas={"dataset": first}, **members)
    assert _problems(pack) == [
        f"garden: {where} is beyond what a pack's registration copies: " + VALUES_PROBLEM
    ]
    exact = {
        "concept": _zeros(MAX_PACK_VALUES - 11),
        "leaf kind": _zeros(MAX_PACK_VALUES - 11),
        "analysis": _zeros(MAX_PACK_VALUES - 61),
    }[site]
    assert PackRegistry(
        [_pack(extension_schemas={"dataset": exact}, **members)], core_version=CORE
    ).ids == ("garden",)


def test_each_pack_has_an_allowance_of_its_own() -> None:
    """Two packs, each at its limit and together twice over it, register: the allowance is
    never shared across packs."""
    # Each pack's leaf kind, two values, is charged after its schema: each pack at its limit.
    given = [
        _pack(pack, extension_schemas={"dataset": _zeros(MAX_PACK_VALUES - 2)},
              leaf_kinds={f"{pack}.wilted": _Leaf({"type": "object"})})
        for pack in ("garden", "orchard")
    ]  # fmt: skip
    assert PackRegistry(given, core_version=CORE).leaf_kinds() == [
        "garden.wilted",
        "orchard.wilted",
    ]
    over = [given[0], _pack("orchard", extension_schemas={"dataset": _zeros(MAX_PACK_VALUES + 1)})]
    assert _problems(*over) == [
        "orchard: its extension schema for dataset is beyond what a pack's registration copies: "
        + VALUES_PROBLEM
    ]


class _Counted(_Analysis):
    def __init__(self, entry: AnalysisDescriptor, reads: list[str]) -> None:
        super().__init__(entry)
        self.reads = reads

    @property
    def entry(self) -> AnalysisDescriptor:
        self.reads.append("entry")
        return self._entry


def _concept_named(id: str, values: int) -> ConceptDescriptor:
    made = _concept(values)
    return made.model_copy(update={"id": id})


@pytest.mark.parametrize("site", ["concepts", "extension schemas", "leaf kinds", "analyses"])
def test_a_pack_is_named_once_and_nothing_more_is_copied_once_its_allowance_is_passed(
    site: str,
) -> None:
    """At each charged site, the item that passes the allowance is followed by two more of the
    same site and by every later site: the pack is named once, and none of them is read (the
    leaf kinds, read before the analyses, are read when the analyses' site trips)."""
    reads: list[str] = []
    entries: list[str] = []
    big = MAX_PACK_VALUES + 1
    members: dict[str, Any] = {
        "concepts": (_concept_named("garden:a", 20), _concept_named("garden:b", 20)),
        "extension_schemas": {"dataset": _zeros(5), "table": _zeros(5)},
        "leaf_kinds": {f"garden.k{n}": _Leaf({"type": "object"}, reads) for n in range(2)},
        "analyses": tuple(_Counted(_entry(f"garden.a{n}"), entries) for n in range(2)),
    }
    if site == "concepts":
        members["concepts"] = (_concept_named("garden:big", big), *members["concepts"])
        where = "its 1st concept"
    elif site == "extension schemas":
        members["extension_schemas"] = {"column": _zeros(big), **members["extension_schemas"]}
        where = "its extension schema for column"
    elif site == "leaf kinds":
        members["leaf_kinds"] = {"garden.big": _Leaf(_zeros(big)), **members["leaf_kinds"]}
        where = "its leaf kind garden.big's schema"
    else:
        members["analyses"] = (_Analysis(_analysis(big)), *members["analyses"])
        where = "its 1st analysis's entry"
    found = _problems(_pack(**members))
    assert found == [
        f"garden: {where} is beyond what a pack's registration copies: " + VALUES_PROBLEM
    ]
    assert entries == []
    assert reads == (["schema", "schema"] if site == "analyses" else [])


def test_a_limit_the_pack_s_own_code_raises_is_its_failure_not_the_allowance() -> None:
    class Raising(_Leaf):
        @property
        def schema(self) -> Any:
            raise JsonTooLarge("pack_values", MAX_PACK_VALUES)

    assert _problems(_pack(leaf_kinds={"garden.wilted": Raising({})})) == [
        "garden: its leaf kind garden.wilted's schema could not be read"
    ]


def test_only_the_copy_s_own_trip_is_the_allowance_s() -> None:
    """``_Reads`` takes a ``JsonTooLarge`` as the allowance's trip only when it is the one the
    copy raised, by identity, even after the allowance has tripped: another, of the same limit's
    name, is the member's failure."""
    problems: list[str] = []
    reads = pack_api._Reads("garden", problems)  # pyright: ignore[reportPrivateUsage]
    allowance = pack_api.pack_allowance()
    with pytest.raises(JsonTooLarge):
        allowance.trip("pack_values", MAX_PACK_VALUES)
    assert allowance.tripped is not None

    def raising() -> None:
        raise JsonTooLarge("pack_values", MAX_PACK_VALUES)

    def tripping() -> None:
        assert allowance.tripped is not None
        raise allowance.tripped

    assert reads("leaf kind garden.a's schema", raising, allowance) == (False, None)
    assert reads("leaf kind garden.b's schema", tripping, allowance) == (False, None)
    assert problems == [
        "garden: its leaf kind garden.a's schema could not be read",
        "garden: its leaf kind garden.b's schema is beyond what a pack's registration copies: "
        + VALUES_PROBLEM,
    ]


def test_the_charged_sites_are_named() -> None:
    assert pack_api.CHARGED == (
        "concepts",
        "extension schemas",
        "leaf kinds' schemas",
        "analyses' entries",
    )
    made = pack_api.pack_allowance()
    assert (made.most_values, made.most_characters, made.text) == (
        MAX_PACK_VALUES,
        MAX_PACK_CHARACTERS,
        MAX_PACK_CHARACTERS,
    )
    assert made.names == ("pack_values", "pack_characters", "pack_characters")
    assert pack_api.pack_allowance() is not made


def test_the_loader_reports_a_trip_as_a_problem_never_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the server's loader, the allowance's trip is one of the registry's problems (no
    exception escapes ``_registry``); an entry at the limit, whose ``params`` the core copies
    again uncharged, registers, and is then refused only for what ``list_analyses`` would
    serve (D421)."""
    monkeypatch.setattr(loader, "GUARD_DUCKDB", False)
    label = "the module garden_module (pack garden)"
    over = loader._registry(  # pyright: ignore[reportPrivateUsage]
        [_pack(analyses=(_Analysis(_analysis(MAX_PACK_VALUES + 1)),))], ["garden_module"]
    )
    assert over == (
        None,
        [
            f"{label}: its 1st analysis's entry is beyond what a pack's registration copies: "
            + VALUES_PROBLEM
        ],
    )
    at = loader._registry(  # pyright: ignore[reportPrivateUsage]
        [_pack(analyses=(_Analysis(_analysis(MAX_PACK_VALUES)),))], ["garden_module"]
    )
    assert at[0] is None
    assert [problem.split(":")[0] for problem in at[1]] == [
        "list_analyses would serve 509451 JSON values, more than the 200000 allowed "
        "(MAX_SERVED_VALUES)"
    ]


# --- Numbers JSON text carries unchanged ------------------------------------------------------

LARGEST = float(2**53 - 1)
BEYOND = (float(2**53), -float(2**53), float(2**53 + 2), -float(2**53 + 2))


@pytest.mark.parametrize("number", BEYOND)
def test_a_float_beyond_the_safe_integers_is_refused_in_a_leaf_schema(number: float) -> None:
    pack = _pack(leaf_kinds={"garden.wilted": _Leaf({"type": "number", "maximum": number})})
    assert _problems(pack) == [
        "garden: the schema of leaf kind garden.wilted is not a JSON value: it holds a number "
        "beyond ±(2^53 - 1)"
    ]


@pytest.mark.parametrize("number", BEYOND)
@pytest.mark.parametrize("member", ["params", "returns"])
def test_a_float_beyond_the_safe_integers_is_refused_in_params_and_returns(
    number: float, member: str
) -> None:
    made = _entry("garden.growth")
    cast(dict[str, JsonValue], getattr(made.fields, member))["maximum"] = number
    assert _problems(_pack(analyses=(_Analysis(made),))) == [
        "garden: its 1st analysis's entry could not be read"
    ]


@pytest.mark.parametrize("number", [LARGEST, -LARGEST, float(2**52), 0.5])
def test_the_largest_safe_float_is_kept_as_it_was_given(number: float) -> None:
    made = _entry("garden.growth", {"type": "number", "maximum": number})
    made.fields.returns["minimum"] = number
    registry = PackRegistry(
        [
            _pack(
                leaf_kinds={"garden.wilted": _Leaf({"type": "number", "maximum": number})},
                analyses=(_Analysis(made),),
            )
        ],
        core_version=CORE,
    )
    kept = registry.leaf_schemas()["garden.wilted"]["maximum"]
    assert type(kept) is float
    assert kept == number
    [listed] = registry.analysis_entries()
    assert listed.fields.params["maximum"] == number
    assert listed.fields.returns["minimum"] == number


# --- The registry's counts ---------------------------------------------------------------------


def _many(total: int, kinds: bool, order: int = 1) -> list[Pack]:
    """``total`` analyses or leaf kinds over 16 packs, the first packs one more, each pack of a
    version of its own; given in pack id order, or reversed with ``order`` -1."""
    given: list[Pack] = []
    for p in range(16):
        pack = f"p{p:02d}"
        count = total // 16 + (1 if p < total % 16 else 0)
        members: dict[str, Any] = (
            {"leaf_kinds": {f"{pack}.k{n:04d}": _Leaf({"type": "object"}) for n in range(count)}}
            if kinds
            else {"analyses": tuple(_Analysis(_entry(f"{pack}.a{n:04d}")) for n in range(count))}
        )
        version = f"1.{p}.0"
        given.append(
            Pack(manifest=manifest(id=pack, version=version, requires_core=">=0"), **members)
        )
    return given[::order]


@pytest.mark.parametrize(
    ("kinds", "most", "name", "what"),
    [
        (False, MAX_PACK_ANALYSES, "MAX_PACK_ANALYSES", "analyses"),
        (True, MAX_LEAF_KINDS, "MAX_LEAF_KINDS", "leaf kinds"),
    ],
)
def test_the_packs_counts_are_capped_in_all_naming_every_share(
    kinds: bool, most: int, name: str, what: str
) -> None:
    for order in (1, -1):
        registry = PackRegistry(_many(most, kinds, order), core_version=CORE)
        assert len(registry.leaf_kinds() if kinds else registry.analysis_versions()) == most
        shares = ", ".join(
            f"p{p:02d} has {(most + 1) // 16 + (1 if p < (most + 1) % 16 else 0)}"
            for p in range(16)
        )
        assert _problems(*_many(most + 1, kinds, order)) == [
            f"the packs have {most + 1} {what} in all, more than the {most} allowed ({name}): "
            + shares
        ]
    assert most == 1_000


@pytest.mark.parametrize("kinds", [False, True])
def test_a_pack_with_no_share_is_named_at_zero(kinds: bool) -> None:
    """A pack that gives none of what is counted is named with its share of 0."""
    given = _many(1_001, kinds)
    members: dict[str, Any] = (
        {"analyses": (_Analysis(_entry("zz.a")),)}
        if kinds
        else {"leaf_kinds": {"zz.k": _Leaf({"type": "object"})}}
    )
    other = Pack(manifest=manifest(id="zz", version="9.0.0"), **members)
    [found] = _problems(*given, other)
    assert found.endswith(", p15 has 62, zz has 0")


def test_the_shares_are_in_pack_id_order_whatever_the_modules_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the loader, with labels: the modules are named against the packs' ids (``z_…``
    gives ``p00``), and the shares are still in pack id order."""
    monkeypatch.setattr(loader, "GUARD_DUCKDB", False)
    for kinds, name in ((False, "MAX_PACK_ANALYSES"), (True, "MAX_LEAF_KINDS")):
        given = _many(1_001, kinds)
        names = [f"{chr(ord('z') - n)}_module" for n in range(16)]
        registry, problems = loader._registry(  # pyright: ignore[reportPrivateUsage]
            given, names
        )
        assert registry is None
        [found] = problems
        assert f"({name}): " in found
        shares = found.split(f"({name}): ")[1].split(", ")
        assert [share.split(" (pack ")[1].split(")")[0] for share in shares] == [
            f"p{p:02d}" for p in range(16)
        ]
        assert shares[0] == "the module z_module (pack p00) has 63"


# --- What registration reads before the counts refuse ---------------------------------------------


class _Schema(dict[str, JsonValue]):
    """An extension schema that records each read of its members (``items``)."""

    def __init__(self, reads: list[str]) -> None:
        super().__init__({"type": "object"})
        self.reads = reads

    def items(self) -> Any:
        self.reads.append("schema")
        return super().items()


def _named_concept(id: str) -> ConceptDescriptor:
    return ConceptDescriptor.model_validate(
        {"kind": "concept", "id": id, "version": 1, "label": "Bloom", "fields": {"sort": "value"}}
    )


SITES = {
    "concepts": MAX_PACK_CONCEPTS,
    "extension schemas": len(RELEASE_KINDS),
    "leaf kinds": MAX_LEAF_KINDS,
    "analyses": MAX_PACK_ANALYSES,
}
"""Each charged site, and the most of its items registration reads."""


def _site(site: str, pack: str, count: int, reads: list[str], first: int = 0) -> dict[str, Any]:
    """``count`` items of ``site`` for ``pack``, each read recorded in ``reads``; ``first``
    numbers them from where an earlier pack's stopped. Past the site's bound, where no read
    reaches them, the items are one object given many times."""
    distinct = count <= SITES[site]
    if site == "concepts":
        one = _named_concept(f"{pack}:c")
        return {
            "concepts": tuple(
                _named_concept(f"{pack}:c{first + n:06d}") if distinct else one
                for n in range(count)
            )
        }
    if site == "extension schemas":
        valid = {kind: _Schema(reads) for kind in RELEASE_KINDS[:count]}
        unknown = _Schema(reads)
        return {
            "extension_schemas": {
                **valid,
                **{f"x{n:06d}": unknown for n in range(count - len(valid))},
            }
        }
    if site == "leaf kinds":
        leaf = _Leaf({}, reads)
        return {"leaf_kinds": {f"{pack}.k{first + n:06d}": leaf for n in range(count)}}
    analysis = _Counted(_entry(f"{pack}.a"), reads)
    return {
        "analyses": tuple(
            _Counted(_entry(f"{pack}.a{first + n:06d}"), reads) if distinct else analysis
            for n in range(count)
        )
    }


def _counting_concepts(monkeypatch: pytest.MonkeyPatch, reads: list[str]) -> None:
    real = pack_api._read_back  # pyright: ignore[reportPrivateUsage]

    def counted(adapter: Any, given: Any, allowance: Any) -> Any:
        if adapter is pack_api._CONCEPT:  # pyright: ignore[reportPrivateUsage]
            reads.append("concept")
        return real(adapter, given, allowance)

    monkeypatch.setattr(pack_api, "_read_back", counted)


@pytest.mark.parametrize("site", list(SITES))
@pytest.mark.parametrize("packs", [1, 16])
def test_past_its_bound_no_charged_site_reads_more(
    site: str, packs: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At each charged site, a hundred times the most it reads, in one pack or spread over
    sixteen, is refused after no read at all where a count refuses it (concepts, leaf kinds,
    analyses), and after at most the release kinds' worth where the kinds bound the site
    (extension schemas, the rest named and neither copied nor checked)."""
    reads: list[str] = []
    _counting_concepts(monkeypatch, reads)
    bound = SITES[site]
    given = [
        Pack(
            manifest=manifest(id=f"p{p:02d}", requires_core=">=0"),
            **_site(site, f"p{p:02d}", 100 * bound // packs, reads),
        )
        for p in range(packs)
    ]
    found = _problems(*given)
    if site == "extension schemas":
        assert len(reads) == packs * len(RELEASE_KINDS)
        others = 100 * bound // packs - len(RELEASE_KINDS)
        assert len(found) == packs * 9
        assert sum("which is not a release descriptor kind" in one for one in found) == packs * 8
        assert [one for one in found if " more problems " in one] == [
            f"p{p:02d}: {others - 8} more problems with its extension schemas" for p in range(packs)
        ]
        return
    assert reads == []
    what = {"concepts": "concepts", "leaf kinds": "leaf kinds", "analyses": "analyses"}[site]
    name = {"concepts": "MAX_PACK_CONCEPTS", "leaf kinds": "MAX_LEAF_KINDS",
            "analyses": "MAX_PACK_ANALYSES"}[site]  # fmt: skip
    assert found == [
        f"the packs have {100 * bound} {what} in all, more than the {bound} allowed ({name}): "
        + ", ".join(f"p{p:02d} has {100 * bound // packs}" for p in range(packs))
    ]


@pytest.mark.parametrize("site", ["concepts", "leaf kinds", "analyses"])
def test_a_pack_that_fits_is_read_in_full_and_a_later_one_over_is_not(
    site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At each counted site, a first pack at the cap is read in full, once each (the cap's
    worth, not one more), and a later pack of a hundred times the cap is not read at all."""
    reads: list[str] = []
    _counting_concepts(monkeypatch, reads)
    bound = SITES[site]
    first = Pack(
        manifest=manifest(id="garden", requires_core=">=0"),
        **_site(site, "garden", bound, reads),
    )
    later = Pack(
        manifest=manifest(id="orchard", requires_core=">=0"),
        **_site(site, "orchard", 100 * bound, reads),
    )
    found = _problems(first, later)
    assert len(reads) == bound
    assert len(found) == 1
    assert found[0].endswith(f"garden has {bound}, orchard has {100 * bound}")


class _Id(str):
    """A concept id that counts each comparison and each hash of it: the work of finding the ids
    given twice."""

    work: ClassVar[list[int]] = [0]

    def __eq__(self, other: object) -> bool:
        _Id.work[0] += 1
        return str.__eq__(self, other)

    def __hash__(self) -> int:
        _Id.work[0] += 1
        return str.__hash__(self)


@pytest.mark.parametrize("given", [MAX_PACK_CONCEPTS // 2, MAX_PACK_CONCEPTS])
def test_the_duplicate_concepts_are_found_in_linear_work(
    given: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concepts given twice are found in work linear in the pack's concepts, at most eight
    comparisons and hashes of an id each (a count per id would be ``given`` each), and named in id
    order, the first eight and the rest counted, whatever order they were given in."""
    real = pack_api._read_back  # pyright: ignore[reportPrivateUsage]

    def counted(adapter: Any, read: Any, allowance: Any) -> Any:
        copy = real(adapter, read, allowance)
        if adapter is pack_api._CONCEPT:  # pyright: ignore[reportPrivateUsage]
            return copy.model_copy(update={"id": _Id(copy.id)})
        return copy

    monkeypatch.setattr(pack_api, "_read_back", counted)
    distinct = given // 3
    # Each id is given three times or more, its first appearances out of id order.
    order = [211 * n % distinct for n in range(given)]
    assert order[:8] != sorted(order[:8])
    _Id.work[0] = 0
    found = _problems(_pack(concepts=tuple(_named_concept(f"garden:c{n:04d}") for n in order)))
    assert _Id.work[0] <= 8 * given, _Id.work[0]
    assert found == [
        *(f"garden: concept garden:c{n:04d} is registered twice" for n in range(8)),
        f"garden: {distinct - 8} more problems with its concepts",
    ]


def test_the_caps_worth_is_read_in_full_and_one_more_is_refused_unread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the caps every concept, kind and analysis is read once; one more of each, given by a
    later pack, is not read at all."""
    ideas: list[str] = []
    _counting_concepts(monkeypatch, ideas)
    reads: list[str] = []
    entries: list[str] = []
    first = Pack(
        manifest=manifest(id="garden", requires_core=">=0"),
        concepts=tuple(_named_concept(f"garden:c{n:04d}") for n in range(MAX_PACK_CONCEPTS)),
        leaf_kinds={f"garden.k{n:04d}": _Leaf({}, reads) for n in range(MAX_LEAF_KINDS)},
        analyses=tuple(
            _Counted(_entry(f"garden.a{n:04d}"), entries) for n in range(MAX_PACK_ANALYSES)
        ),
    )
    registry = PackRegistry([first], core_version=CORE)
    assert (len(ideas), len(reads), len(entries)) == (
        MAX_PACK_CONCEPTS,
        MAX_LEAF_KINDS,
        MAX_PACK_ANALYSES,
    )
    assert len(registry.concepts()) == MAX_PACK_CONCEPTS
    assert len(registry.leaf_kinds()) == MAX_LEAF_KINDS
    ideas.clear()
    reads.clear()
    entries.clear()
    later: list[str] = []
    second = Pack(
        manifest=manifest(id="orchard", requires_core=">=0"),
        concepts=(_named_concept("orchard:c"),),
        leaf_kinds={"orchard.k": _Leaf({}, later)},
        analyses=(_Counted(_entry("orchard.a"), later),),
    )
    assert len(_problems(first, second)) == 3
    assert later == []
    assert (len(ideas), len(reads), len(entries)) == (
        MAX_PACK_CONCEPTS,
        MAX_LEAF_KINDS,
        MAX_PACK_ANALYSES,
    )


def test_every_accessor_of_one_pack_refuses_an_unknown_pack() -> None:
    from aibi.core.schema.pack_api import UnknownPack

    registry = PackRegistry([_pack()], core_version=CORE)
    for accessor in (registry.manifest, registry.label, registry.caveat_codes, registry.pack):
        with pytest.raises(UnknownPack):
            accessor("orchard")
    assert registry.label("garden") == "garden"


def test_what_one_pack_cannot_take_is_left_for_the_next() -> None:
    """The room is the caps' own for each count: a pack's analyses take none of the kinds' room,
    and a pack over what is left takes none of it, so a later pack that fits is read in full
    (its share named all the same)."""
    entries: list[str] = []
    reads: list[str] = []
    analyses = Pack(
        manifest=manifest(id="garden", requires_core=">=0"),
        analyses=tuple(
            _Counted(_entry(f"garden.a{n:04d}"), entries) for n in range(MAX_PACK_ANALYSES)
        ),
    )
    kinds = Pack(
        manifest=manifest(id="orchard", requires_core=">=0"),
        leaf_kinds={f"orchard.k{n:03d}": _Leaf({}, reads) for n in range(500)},
    )
    registry = PackRegistry([analyses, kinds], core_version=CORE)
    assert (len(entries), len(reads)) == (MAX_PACK_ANALYSES, 500)
    assert len(registry.leaf_kinds()) == 500
    reads.clear()
    over = Pack(
        manifest=manifest(id="apiary", requires_core=">=0"),
        leaf_kinds={f"apiary.k{n:04d}": _Leaf({}) for n in range(MAX_LEAF_KINDS + 1)},
    )
    assert _problems(over, kinds) == [
        f"the packs have {MAX_LEAF_KINDS + 501} leaf kinds in all, more than the "
        f"{MAX_LEAF_KINDS} allowed (MAX_LEAF_KINDS): apiary has {MAX_LEAF_KINDS + 1}, "
        "orchard has 500"
    ]
    assert len(reads) == 500


def _kinds(pack: str, count: int, *extra: str) -> Pack:
    leaves = {f"{pack}.k{n:04d}": _Leaf({}) for n in range(count)}
    leaves.update({name: _Leaf({}) for name in extra})
    return Pack(manifest=manifest(id=pack, requires_core=">=0"), leaf_kinds=leaves)


def test_past_the_room_the_problems_listed_follow_the_order_and_the_refusal_does_not() -> None:
    """Once the room is out, a pack's kinds go unread and unchecked (D421): which of their
    problems are listed depends on the order the packs are given in, but the count's refusal,
    naming every share, is reached whatever the order."""
    aa = _kinds("aa", 599, "notnamespaced")
    bb = _kinds("bb", 600)
    count = (
        "the packs have 1200 leaf kinds in all, more than the 1000 allowed (MAX_LEAF_KINDS): "
        "aa has 600, bb has 600"
    )
    assert _problems(aa, bb) == ["aa: leaf kind notnamespaced is not aa.<name>", count]
    assert _problems(bb, aa) == [count]


@pytest.mark.parametrize("site", ["concepts", "leaf kinds", "analyses"])
def test_a_pack_whose_manifest_cannot_be_read_reads_nothing_counted(
    site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pack that will not be kept reads none of its concepts, leaf kinds or analyses, and
    leaves the room to the packs after it: their items are still read and checked, so its own
    refusal hides none of their problems, in either order."""
    reads: list[str] = []
    _counting_concepts(monkeypatch, reads)
    nameless = Pack(manifest=cast(Any, object()), **_site(site, "zz", SITES[site], reads))
    bad: dict[str, Any] = {
        "concepts": {"concepts": (_named_concept("notyy:c"),)},
        "leaf kinds": {"leaf_kinds": {"notnamespaced": _Leaf({})}},
        "analyses": {"analyses": (_Analysis(_entry("notyy.a")),)},
    }[site]
    expected = {
        "concepts": "yy: concept notyy:c is not in its namespace yy:",
        "leaf kinds": "yy: leaf kind notnamespaced is not yy.<name>",
        "analyses": "yy: analysis notyy.a is not yy.<name>",
    }[site]
    yy = Pack(manifest=manifest(id="yy", requires_core=">=0"), **bad)
    for given in ((nameless, yy), (yy, nameless)):
        found = _problems(*given)
        assert expected in found
        assert any("its manifest could not be read" in one for one in found)
    # Only ``yy``'s one concept is read; nothing of the nameless pack's.
    assert reads == (["concept"] * 2 if site == "concepts" else [])


@pytest.mark.parametrize("site", ["concepts", "leaf kinds", "analyses"])
def test_what_one_pack_takes_is_gone_for_the_next(
    site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caps are counted in all, not per pack: a first pack takes three fifths of a cap and is
    read in full; a second of three fifths, within the cap alone but not within what is left,
    is not read, and the count refuses naming both."""
    reads: list[str] = []
    _counting_concepts(monkeypatch, reads)
    bound = SITES[site]
    part = 3 * bound // 5
    first = Pack(
        manifest=manifest(id="garden", requires_core=">=0"), **_site(site, "garden", part, reads)
    )
    second = Pack(
        manifest=manifest(id="orchard", requires_core=">=0"),
        **_site(site, "orchard", part, reads),
    )
    found = _problems(first, second)
    assert len(reads) == part
    name = {"concepts": "MAX_PACK_CONCEPTS", "leaf kinds": "MAX_LEAF_KINDS",
            "analyses": "MAX_PACK_ANALYSES"}[site]  # fmt: skip
    assert found == [
        f"the packs have {2 * part} {site} in all, more than the {bound} allowed ({name}): "
        f"garden has {part}, orchard has {part}"
    ]


# --- Each site's problems: the first eight named, the rest counted ------------------------------


def test_a_site_names_eight_of_its_problems() -> None:
    assert pack_api.SHOWN_PROBLEMS == 8


_SEVERITY = next(iter(Severity))


def _cited(count: int) -> dict[str, Any]:
    """Analyses citing ``count`` codes the pack does not declare, 32 an analysis (an entry lists
    at most ``MAX_ENTRIES``)."""
    analyses: list[_Analysis] = []
    for first in range(0, count, 32):
        entry = _entry(f"garden.a{first // 32:02d}")
        codes = [f"garden.C{n:04d}" for n in range(first, min(count, first + 32))]
        fields = entry.fields.model_copy(update={"caveats": codes})
        analyses.append(_Analysis(entry.model_copy(update={"fields": fields})))
    return {"analyses": tuple(analyses)}


BAD: dict[str, tuple[Callable[[int], dict[str, Any]], Callable[[int], str]]] = {
    "concepts": (
        lambda count: {"concepts": tuple(_named_concept(f"bed:c{n:04d}") for n in range(count))},
        lambda n: f"concept bed:c{n:04d} is not in its namespace garden:",
    ),
    "ontology systems": (
        lambda count: {"ontology_systems": {n: object() for n in range(count)}},
        lambda n: "an ontology system name is empty, too long or not Unicode text",
    ),
    "extension schemas": (
        lambda count: {"extension_schemas": {f"x{n:04d}": {} for n in range(count)}},
        lambda n: f"extension schema for x{n:04d}, which is not a release descriptor kind",
    ),
    "leaf kinds": (
        lambda count: {"leaf_kinds": {f"k{n:04d}": _Leaf({}) for n in range(count)}},
        lambda n: f"leaf kind k{n:04d} is not garden.<name>",
    ),
    "translators": (
        lambda count: {"translators": {f"t{n:04d}": object() for n in range(count)}},
        lambda n: f"translator format t{n:04d} is not garden.<name>",
    ),
    "analyses": (
        lambda count: {"analyses": tuple(_Analysis(_entry(f"bed.a{n:04d}")) for n in range(count))},
        lambda n: f"analysis bed.a{n:04d} is not garden.<name>",
    ),
    "requirement predicates": (
        lambda count: {"requirement_predicates": {f"p-{n:04d}": object() for n in range(count)}},
        lambda n: f"requirement predicate p-{n:04d} is not an identifier",
    ),
    "caveat codes": (
        lambda count: {"caveat_codes": {f"bed.C{n:04d}": _SEVERITY for n in range(count)}},
        lambda n: f"caveat code bed.C{n:04d} is not garden.<CODE>",
    ),
    "analyses' caveats": (
        _cited,
        lambda n: f"analysis garden.a{n // 32:02d} cites garden.C{n:04d}, which is not declared",
    ),
    "wording": (
        lambda count: {"wording": {f"x{n:04d}": "Text" for n in range(count)}},
        lambda n: f"wording for x{n:04d}, which is not a core caveat code",
    ),
}
"""Each site of a pack's registration whose items may each be a problem: a pack member giving
``count`` bad items, and the problem of the ``n``th, in the order given."""


@pytest.mark.parametrize("site", list(BAD))
@pytest.mark.parametrize("count", [1, 8, 9, 10, 200])
def test_each_site_names_its_first_eight_problems_and_counts_the_rest(
    site: str, count: int
) -> None:
    given, problem = BAD[site]
    found = _problems(_pack(**given(count)))
    rest = {9: ["garden: 1 more problem with its " + site]}.get(
        count, [f"garden: {count - 8} more problems with its {site}"] if count > 8 else []
    )
    assert found == [*(f"garden: {problem(n)}" for n in range(min(count, 8))), *rest]


@pytest.mark.parametrize(
    "site",
    [
        "ontology systems",
        "extension schemas",
        "translators",
        "requirement predicates",
        "caveat codes",
        "wording",
    ],
)
def test_a_hundred_thousand_bad_items_make_nine_problems(site: str) -> None:
    """A site no count caps, given a hundred thousand bad items, still makes nine problems."""
    given, problem = BAD[site]
    found = _problems(_pack(**given(100_000)))
    assert found == [
        *(f"garden: {problem(n)}" for n in range(8)),
        f"garden: 99992 more problems with its {site}",
    ]


def test_a_key_that_is_not_text_is_named_in_the_core_s_words() -> None:
    """An extension schema's key that is a ``str`` but not Unicode text (a lone surrogate) is
    named as no name, never quoted as a kind."""
    found = _problems(_pack(extension_schemas={"\ud800": {}}))
    assert found == ["garden: extension schema for (a key that is not text) is not a name"]


class _Pairs(Mapping[Any, Any]):
    """A mapping whose ``items()`` gives ``pairs`` as they are, a key given twice included."""

    def __init__(self, pairs: list[tuple[Any, Any]]) -> None:
        self.pairs = pairs

    def __getitem__(self, key: Any) -> Any:
        raise KeyError(key)

    def __iter__(self) -> Iterator[Any]:
        return iter([key for key, _ in self.pairs])

    def __len__(self) -> int:
        return len(self.pairs)

    def items(self) -> Any:
        return self.pairs


_CORE_CODE = next(iter(CaveatCode)).value

EACH: dict[str, tuple[str, Callable[[int], dict[str, Any]], Callable[[int], str]]] = {
    "ontology systems given twice": (
        "ontology systems",
        lambda count: {"ontology_systems": _Pairs([("soil", object())] * (count + 1))},
        lambda n: "ontology system soil is given twice",
    ),
    "extension schemas not text": (
        "extension schemas",
        lambda count: {"extension_schemas": {chr(0xD800 + n): {} for n in range(count)}},
        lambda n: "extension schema for (a key that is not text) is not a name",
    ),
    "extension schemas given twice": (
        "extension schemas",
        lambda count: {
            "extension_schemas": _Pairs([("dataset", {"type": "object"})] * (count + 1))
        },
        lambda n: "extension schema for dataset is given twice",
    ),
    "leaf kinds not text": (
        "leaf kinds",
        lambda count: {"leaf_kinds": {n: _Leaf({}) for n in range(count)}},
        lambda n: "leaf kind (a key that is not text) is not garden.<name>",
    ),
    "leaf kinds given twice": (
        "leaf kinds",
        lambda count: {"leaf_kinds": _Pairs([("garden.k", _Leaf({}))] * (count + 1))},
        lambda n: "leaf kind garden.k is given twice",
    ),
    "translators not text": (
        "translators",
        lambda count: {"translators": {n: object() for n in range(count)}},
        lambda n: "translator format (a key that is not text) is not garden.<name>",
    ),
    "translators given twice": (
        "translators",
        lambda count: {"translators": _Pairs([("garden.t", object())] * (count + 1))},
        lambda n: "translator format garden.t is given twice",
    ),
    "requirement predicates given twice": (
        "requirement predicates",
        lambda count: {"requirement_predicates": _Pairs([("ripe", object())] * (count + 1))},
        lambda n: "requirement predicate ripe is given twice",
    ),
    "caveat codes not text": (
        "caveat codes",
        lambda count: {"caveat_codes": dict.fromkeys(range(count), _SEVERITY)},
        lambda n: "caveat code (a key that is not text) is not garden.<CODE>",
    ),
    "caveat codes without a severity": (
        "caveat codes",
        lambda count: {"caveat_codes": {f"garden.C{n:04d}": None for n in range(count)}},
        lambda n: f"caveat code garden.C{n:04d} has no severity",
    ),
    "caveat codes given twice": (
        "caveat codes",
        lambda count: {"caveat_codes": _Pairs([("garden.C", _SEVERITY)] * (count + 1))},
        lambda n: "caveat code garden.C is given twice",
    ),
    "wording of no code and not text": (
        "wording",
        lambda count: {"wording": {f"x{n:04d}": 1 for n in range((count + 1) // 2)}},
        lambda n: (
            f"wording for x{n // 2:04d}, which is not a core caveat code"
            if n % 2 == 0
            else f"the wording for x{n // 2:04d} is not Unicode text"
        ),
    ),
    "wording given twice": (
        "wording",
        lambda count: {"wording": _Pairs([(_CORE_CODE, "Text")] * (count + 1))},
        lambda n: f"the wording for {_CORE_CODE} is given twice",
    ),
}
"""Each other problem a site words about its items: ``count`` of them (``count + 1`` items where
one key is given ``count`` more times), and the problem of the ``n``th."""


@pytest.mark.parametrize("problem", list(EACH))
@pytest.mark.parametrize("count", [8, 10])
def test_every_problem_a_site_words_is_one_of_its_eight(problem: str, count: int) -> None:
    site, given, worded = EACH[problem]
    found = _problems(_pack(**given(count)))
    rest = [f"garden: 2 more problems with its {site}"] if count == 10 else []
    assert found == [*(f"garden: {worded(n)}" for n in range(8)), *rest]


def _cites(codes: list[str]) -> dict[str, Any]:
    entry = _entry("garden.a")
    fields = entry.fields.model_copy(update={"caveats": codes})
    return {"analyses": (_Analysis(entry.model_copy(update={"fields": fields})),)}


MIXED: dict[str, tuple[dict[str, Any], str]] = {
    "concepts": (
        {
            "concepts": (
                *(_named_concept(f"garden:c{n:04d}") for n in range(20)),
                _named_concept("bed:c"),
            )
        },
        "concept bed:c is not in its namespace garden:",
    ),
    "ontology systems": (
        {"ontology_systems": {**{f"s{n}": object() for n in range(20)}, 1: object()}},
        "an ontology system name is empty, too long or not Unicode text",
    ),
    "leaf kinds": (
        {"leaf_kinds": {**{f"garden.k{n}": _Leaf({}) for n in range(20)}, "k": _Leaf({})}},
        "leaf kind k is not garden.<name>",
    ),
    "translators": (
        {"translators": {**{f"garden.t{n}": object() for n in range(20)}, "t": object()}},
        "translator format t is not garden.<name>",
    ),
    "analyses": (
        {
            "analyses": (
                *(_Analysis(_entry(f"garden.a{n}")) for n in range(20)),
                _Analysis(_entry("bed.a")),
            )
        },
        "analysis bed.a is not garden.<name>",
    ),
    "requirement predicates": (
        {"requirement_predicates": {**{f"p{n}": object() for n in range(20)}, "p-": object()}},
        "requirement predicate p- is not an identifier",
    ),
    "caveat codes": (
        {"caveat_codes": {**{f"garden.C{n}": _SEVERITY for n in range(20)}, "bed.C": _SEVERITY}},
        "caveat code bed.C is not garden.<CODE>",
    ),
    "analyses' caveats": (
        {
            "caveat_codes": {f"garden.C{n}": _SEVERITY for n in range(20)},
            **_cites([*(f"garden.C{n}" for n in range(20)), "garden.X"]),
        },
        "analysis garden.a cites garden.X, which is not declared",
    ),
    "wording": (
        {"wording": {**{code.value: "Text" for code in CaveatCode}, "x": "Text"}},
        "wording for x, which is not a core caveat code",
    ),
}
"""Each site given twenty or more items without a problem, then one with."""


@pytest.mark.parametrize("site", list(MIXED))
def test_an_item_without_a_problem_takes_none_of_the_eight(site: str) -> None:
    given, problem = MIXED[site]
    assert _problems(_pack(**given)) == [f"garden: {problem}"]


@pytest.mark.parametrize("site", ["leaf kinds", "analyses"])
def test_the_problems_of_what_cannot_be_listed_are_the_site_s(
    site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every kind and every entry registration admits lists (the check is the registry's
    guarantee); made to fail for ten, eight are named and two counted."""

    def refused(*given: Any) -> Any:
        return TypeAdapter(int).validate_python("not a number")

    monkeypatch.setattr(pack_api, "leaf_kind_entry", refused)
    monkeypatch.setattr(pack_api, "analysis_entry", refused)
    if site == "leaf kinds":
        given: dict[str, Any] = {"leaf_kinds": {f"garden.k{n}": _Leaf({}) for n in range(10)}}
        worded = [
            f"leaf kind garden.k{n} cannot be listed: its name or its schema is beyond what "
            "list_leaf_kinds gives"
            for n in range(8)
        ]
    else:
        given = {"analyses": tuple(_Analysis(_entry(f"garden.a{n}")) for n in range(10))}
        worded = [
            f"analysis garden.a{n} cannot be listed: a member of its entry is beyond what "
            "list_analyses gives"
            for n in range(8)
        ]
    assert _problems(_pack(**given)) == [
        *(f"garden: {one}" for one in worded),
        f"garden: 2 more problems with its {site}",
    ]


def test_the_count_problems_come_concepts_analyses_kinds() -> None:
    """Every count over its cap is named, in one order: concepts, analyses, leaf kinds."""
    over = _pack(
        concepts=tuple(_named_concept(f"garden:c{n:04d}") for n in range(MAX_PACK_CONCEPTS + 1)),
        leaf_kinds={f"garden.k{n:04d}": _Leaf({}) for n in range(MAX_LEAF_KINDS + 1)},
        analyses=tuple(_Analysis(_entry(f"garden.a{n:04d}")) for n in range(MAX_PACK_ANALYSES + 1)),
    )
    found = _problems(over)
    assert [one.split(" in all")[0] for one in found] == [
        f"the packs have {MAX_PACK_CONCEPTS + 1} concepts",
        f"the packs have {MAX_PACK_ANALYSES + 1} analyses",
        f"the packs have {MAX_LEAF_KINDS + 1} leaf kinds",
    ]

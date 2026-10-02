"""The pack API (SPEC §10.1), exercised with a test-only lending-library pack."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import Any, cast

import pytest
from pydantic import JsonValue, ValidationError

from aibi.core.schema.caveats import CaveatCode, Severity
from aibi.core.schema.descriptors import (
    AnalysisDescriptor,
    ConceptDescriptor,
    ConceptFields,
)
from aibi.core.schema.document import Clause, PackLeaf, ValueLeaf
from aibi.core.schema.jsonschemas import OUT_OF_STEPS, Checker, StepBudget
from aibi.core.schema.output import Segment, text
from aibi.core.schema.pack_api import (
    AnalysisInputs,
    Pack,
    PackError,
    PackManifest,
    PackRegistry,
    ReleaseView,
    TranslationNote,
    UnknownPack,
)

CORE = "0.1.0"


def manifest(**members: Any) -> PackManifest:
    base: dict[str, Any] = {
        "id": "library",
        "version": "1.2.0",
        "results_version": 1,
        "requires_core": ">=0.1,<0.2",
    }
    return PackManifest.model_validate({**base, **members})


def concept(id: str) -> ConceptDescriptor:
    return ConceptDescriptor(
        kind="concept", id=id, version=1, label="Loan status", fields=ConceptFields(sort="value")
    )


def entry(id: str) -> AnalysisDescriptor:
    return AnalysisDescriptor.model_validate(
        {
            "kind": "analysis",
            "id": id,
            "version": "1.0.0",
            "label": "Loan rates",
            "fields": {
                "requires": [{"role": "cohorts", "min": 1, "max": 6}],
                "params": {"type": "object"},
                "returns": {"type": "object"},
                "methods": {},
                "assumptions": [],
                "uses_reference": False,
                "assumes_independent_groups": True,
                "cross_dataset": None,
                "caveats": ["SMALL_N", "library.RENEWALS_ESTIMATED"],
            },
        }
    )


class Overdue:
    """``library.overdue``: loans returned late, compiled to a core value leaf."""

    @property
    def schema(self) -> Mapping[str, JsonValue]:
        return {"type": "object", "properties": {"days": {"type": "integer"}}}

    def compile(self, leaf: PackLeaf, release: ReleaseView, pack_version: str) -> Sequence[Clause]:
        days = (leaf.model_extra or {}).get("days", 14)
        return [
            ValueLeaf.model_validate(
                {"kind": "value", "column": "loans.days_late", "range": {"gt": days}}
            )
        ]

    def summary(self, leaf: PackLeaf) -> Sequence[Segment]:
        return [text("loans returned late")]


class Marc:
    def translate(
        self, document: JsonValue
    ) -> tuple[Mapping[str, JsonValue], Sequence[TranslationNote]]:
        return {"aibi": "1"}, []


class LoanRates:
    @property
    def entry(self) -> AnalysisDescriptor:
        return entry("library.loan_rates")

    def run(self, inputs: AnalysisInputs) -> Mapping[str, JsonValue]:
        return {}


def _facet(release: ReleaseView) -> Mapping[str, Sequence[str]]:
    return {"collection": ["books"]}


def _rule(release: ReleaseView, document: Mapping[str, JsonValue]) -> Sequence[str]:
    return ["library.RENEWALS_ESTIMATED"]


LIBRARY = Pack(
    manifest=manifest(),
    concepts=(concept("library:loan_status"),),
    ontology_systems={"LIBRARY-CODES": lambda code: code.startswith("L")},
    extension_schemas={"dataset": {"type": "object"}},
    leaf_kinds={"library.overdue": Overdue()},
    translators={"library.marc": Marc()},
    analyses=(LoanRates(),),
    requirement_predicates={"has_loans": lambda release: True},
    facet=_facet,
    caveat_codes={"library.RENEWALS_ESTIMATED": Severity.WARN},
    caveat_rule=_rule,
    wording={CaveatCode.SMALL_N: "Few loans"},
)
ARCHIVE = Pack(manifest=manifest(id="archive", version="0.3"))


def registry(*packs: Pack) -> PackRegistry:
    return PackRegistry(packs or (LIBRARY, ARCHIVE), core_version=CORE)


def test_the_registry_answers_for_the_packs_listed() -> None:
    packs = registry()
    assert packs.ids == ("archive", "library")
    # The registry keeps a snapshot of the pack given.
    snapshot = packs.pack("library")
    assert snapshot.manifest == LIBRARY.manifest
    assert dict(snapshot.extension_schemas) == dict(LIBRARY.extension_schemas)
    assert [a.entry for a in snapshot.analyses] == [a.entry for a in LIBRARY.analyses]
    assert snapshot.concepts == LIBRARY.concepts
    assert [pack.id for pack in packs.listed(["library", "archive", "library"])] == [
        "archive",
        "library",
    ]
    with pytest.raises(UnknownPack):
        packs.listed(["shelving"])
    assert packs.extension_schemas("dataset", ["library"]) == {"library": {"type": "object"}}
    assert packs.extension_schemas("dataset", ["archive"]) == {}
    assert packs.facets(["archive"]) == []
    assert packs.facets(["library"]) == [_facet]
    assert packs.caveat_rules(["library"]) == [_rule]
    assert packs.wordings(CaveatCode.SMALL_N, ["archive", "library"]) == [("library", "Few loans")]
    assert packs.validators(["library"]) == []
    assert packs.proposers(["library"]) == []


def test_extension_points_are_found_by_their_names() -> None:
    packs = registry()
    assert [c.id for c in packs.concepts()] == ["library:loan_status"]
    validator = packs.ontology_validator("LIBRARY-CODES")
    assert validator is not None
    assert validator("L1")
    assert packs.ontology_validator("OTHER") is None
    assert isinstance(packs.leaf_kind("library.overdue"), Overdue)
    assert packs.leaf_kind("library.missing") is None
    with pytest.raises(UnknownPack):
        packs.leaf_kind("shelving.overdue")
    assert isinstance(packs.translator("library.marc"), Marc)
    analysis = packs.analysis("library.loan_rates")
    assert analysis is not None
    assert analysis.entry.id == "library.loan_rates"
    assert [a.entry.id for a in packs.analyses()] == ["library.loan_rates"]
    assert packs.requirement_predicate("library.has_loans") is not None
    assert packs.requirement_predicate("library.other") is None
    assert packs.importer("library") is None
    assert packs.rebuilder("archive") is None


def test_severities_come_from_the_core_or_the_pack() -> None:
    packs = registry()
    assert packs.severity("TIME_ORIGIN_MISMATCH") is Severity.BLOCK
    assert packs.severity("library.RENEWALS_ESTIMATED") is Severity.WARN
    assert packs.severity("library.OTHER") is None
    assert packs.severity("OTHER") is None
    with pytest.raises(UnknownPack):
        packs.severity("shelving.X")


def test_unknown_packs_are_refused_alike() -> None:
    packs = registry()
    for lookup in (
        lambda: packs.analysis("shelving.count"),
        lambda: packs.leaf_kind("shelving.x"),
        lambda: packs.translator("shelving.x"),
        lambda: packs.requirement_predicate("shelving.x"),
        lambda: packs.severity("shelving.X"),
        lambda: packs.importer("shelving"),
    ):
        with pytest.raises(UnknownPack):
            lookup()
    assert packs.analysis("summary.counts") is None  # core analyses come with M3
    assert packs.analysis("library.missing") is None


def test_the_registry_hands_out_copies() -> None:
    packs = registry()
    schemas: Any = packs.extension_schemas("dataset", ["library"])
    schemas["library"]["type"] = "array"
    snapshot = packs.pack("library")
    cast(dict[str, Any], snapshot.extension_schemas["dataset"])["type"] = "array"
    assert packs.extension_schemas("dataset", ["library"]) == {"library": {"type": "object"}}
    analysis = packs.analysis("library.loan_rates")
    assert analysis is not None
    analysis.entry.fields.caveats.append("library.UNDECLARED")
    assert "library.UNDECLARED" not in analysis.entry.fields.caveats
    assert packs.analyses()[0].entry == LoanRates().entry


def test_read_only_schemas_are_registered() -> None:
    schema = MappingProxyType({"type": "object", "properties": MappingProxyType({"x": {}})})
    pack = replace(LIBRARY, extension_schemas=MappingProxyType({"dataset": schema}))
    packs = registry(pack, ARCHIVE)
    assert packs.extension_schemas("dataset", ["library"]) == {
        "library": {"type": "object", "properties": {"x": {}}}
    }


def test_a_leaf_kind_compiles_to_core_clauses() -> None:
    leaf = PackLeaf.model_validate({"kind": "library.overdue", "days": 30})
    kind = registry().leaf_kind("library.overdue")
    assert kind is not None
    view: Any = object()
    [clause] = kind.compile(leaf, view, "1.2.0")
    assert isinstance(clause, ValueLeaf)
    assert clause.range is not None
    assert clause.range.gt == 30


@pytest.mark.parametrize(
    "members",
    [
        {"id": "summary"},
        {"id": "core"},
        {"id": "value"},
        {"id": "a__b"},
        {"version": "1.2.0-rc1"},
        {"version": "01.2"},
        {"requires_core": "at least 0.1"},
        {"requires_core": ">=0.1,,<0.2"},
        {"requires_core": "!=1.0"},
        {"requires_core": "<2"},
        {"requires_core": ">= 0.1"},
        {"results_version": 0},
    ],
)
def test_manifests_are_checked(members: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        manifest(**members)


def _problems(*packs: Pack, core: str = CORE) -> list[str]:
    with pytest.raises(PackError) as raised:
        PackRegistry(packs, core_version=core)
    return list(raised.value.problems)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"manifest": manifest(requires_core=">=0.2")}, "requires core"),
        ({"concepts": (concept("archive:loan_status"),)}, "not in its namespace"),
        ({"extension_schemas": {"concept": {}}}, "not a release descriptor kind"),
        ({"ontology_systems": {"": lambda code: True}}, "empty, too long or not Unicode"),
        ({"ontology_systems": {1: lambda code: True}}, "empty, too long or not Unicode"),
        ({"leaf_kinds": {1: Overdue()}}, "is not library.<name>"),
        ({"caveat_codes": {1: "x"}}, "caveat"),
        ({"requirement_predicates": {1: lambda release: True}}, "not an identifier"),
        ({"leaf_kinds": {"overdue": Overdue()}}, "leaf kind overdue is not library.<name>"),
        ({"leaf_kinds": {"archive.overdue": Overdue()}}, "is not library.<name>"),
        ({"translators": {"marc": Marc()}}, "translator format"),
        ({"analyses": (LoanRates(), LoanRates())}, "appears twice"),
        ({"requirement_predicates": {"has-loans": lambda release: True}}, "not an identifier"),
        ({"caveat_codes": {"archive.X": Severity.INFO}}, "is not library.<CODE>"),
        ({"caveat_codes": {"library.lower": Severity.INFO}}, "is not library.<CODE>"),
        ({"caveat_codes": {"library.X": "warn"}}, "has no severity"),
        ({"wording": {"library.X": "text"}}, "not a core caveat code"),
    ],
)
def test_packs_are_checked_when_registered(change: dict[str, Any], problem: str) -> None:
    problems = _problems(replace(LIBRARY, **change))
    assert any(problem in found for found in problems), problems


def test_packs_do_not_conflict() -> None:
    twice = _problems(LIBRARY, LIBRARY)
    assert any("registered twice" in problem for problem in twice)
    rival = Pack(
        manifest=manifest(id="archive"),
        ontology_systems={"LIBRARY-CODES": lambda code: True},
    )
    assert any(
        "LIBRARY-CODES is registered by library and archive" in p for p in _problems(LIBRARY, rival)
    )
    assert _problems(core="not a version") == [
        "core version 'not a version' is not a PEP 440 version"
    ]


def test_the_registry_keeps_what_was_registered() -> None:
    systems = {"LIBRARY-CODES": lambda code: True}
    codes = {"library.RENEWALS_ESTIMATED": Severity.WARN}
    concepts = [concept("library:loan_status")]
    pack = replace(LIBRARY, ontology_systems=systems, caveat_codes=codes, concepts=tuple(concepts))
    packs = registry(pack)
    systems.clear()
    codes["library.RENEWALS_ESTIMATED"] = Severity.BLOCK
    codes["library.BOGUS"] = Severity.INFO
    assert packs.ontology_validator("LIBRARY-CODES") is not None
    assert packs.severity("library.RENEWALS_ESTIMATED") is Severity.WARN
    assert packs.severity("library.BOGUS") is None
    with pytest.raises(TypeError):
        packs.pack("library").caveat_codes["library.X"] = Severity.INFO  # type: ignore[index]


def test_a_pack_declares_what_its_analyses_cite() -> None:
    undeclared = replace(LIBRARY, caveat_codes={})
    assert any("cites library.RENEWALS_ESTIMATED" in p for p in _problems(undeclared))
    twice = replace(LIBRARY, concepts=(concept("library:a"), concept("library:a")))
    assert any("library:a is registered twice" in p for p in _problems(twice))


@pytest.mark.parametrize("pack_id", ["rel", "cov", "ep", "model", "dataset", "drv", "leaf", "stat"])
def test_pack_ids_are_not_prefixes_of_core_ids(pack_id: str) -> None:
    with pytest.raises(ValidationError):
        manifest(id=pack_id)


def test_requires_core_bounds_the_core_versions() -> None:
    for specifier in (" ", ""):
        with pytest.raises(ValidationError):
            manifest(requires_core=specifier)


# --- Round 3: nothing handed out or given later changes what was registered ------------------


def _nested_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {"x": {"type": "integer", "enum": [1, 2]}}}


def test_nothing_handed_out_changes_the_registry() -> None:
    packs = registry(replace(LIBRARY, extension_schemas={"dataset": _nested_schema()}), ARCHIVE)
    handed: Any = packs.listed(["library"])[0].extension_schemas["dataset"]
    handed["properties"]["x"]["type"] = "string"
    handed["properties"]["x"]["enum"].append(3)
    whole: Any = packs.pack("library").extension_schemas["dataset"]
    whole["properties"]["x"]["enum"].clear()
    assert packs.extension_schemas("dataset", ["library"]) == {"library": _nested_schema()}
    assert packs.pack("library").extension_schemas["dataset"] == _nested_schema()
    for concepts in (
        packs.concepts(),
        packs.pack("library").concepts,
        packs.listed(["library"])[0].concepts,
    ):
        concepts[0].curation["/label"] = cast(Any, "changed")
        concepts[0].extensions["library"] = {"x": 1}
    assert packs.concepts() == [concept("library:loan_status")]
    analysis = packs.analysis("library.loan_rates")
    assert analysis is not None
    assert not hasattr(analysis, "registered")


def test_nothing_the_caller_changes_after_registration_changes_the_registry() -> None:
    schema = _nested_schema()
    concepts = [concept("library:loan_status")]
    loan_rates = LoanRates()
    stored = loan_rates.entry
    pack = replace(
        LIBRARY,
        extension_schemas={"dataset": schema},
        concepts=tuple(concepts),
        analyses=(_Fixed(stored),),
    )
    packs = registry(pack, ARCHIVE)
    schema["properties"]["x"]["enum"].append(3)
    concepts[0].curation["/label"] = cast(Any, "changed")
    stored.fields.caveats.append("library.UNDECLARED")
    assert packs.extension_schemas("dataset", ["library"]) == {"library": _nested_schema()}
    assert packs.concepts() == [concept("library:loan_status")]
    analysis = packs.analysis("library.loan_rates")
    assert analysis is not None
    assert analysis.entry == LoanRates().entry


class _Fixed:
    """An analysis that hands out the same entry object every time."""

    def __init__(self, entry: AnalysisDescriptor) -> None:
        self._entry = entry

    @property
    def entry(self) -> AnalysisDescriptor:
        return self._entry

    def run(self, inputs: AnalysisInputs) -> Mapping[str, JsonValue]:
        return {}


@pytest.mark.parametrize(
    "schema",
    [
        {1: "integer"},
        {"enum": {1, 2}},
        {"maximum": math.nan},
        {"x": b"bytes"},
        {"description": "caf\udce9"},
    ],
    ids=repr,
)
def test_extension_schemas_are_json_values(schema: dict[Any, Any]) -> None:
    pack = replace(LIBRARY, extension_schemas={"dataset": schema})
    with pytest.raises(PackError, match="extension schema for dataset is not a JSON value"):
        registry(pack, ARCHIVE)


# --- Round 4: every problem collected, schemas of any shape, wordings -----------------------------


def _problems_of(*packs: Pack) -> list[str]:
    with pytest.raises(PackError) as raised:
        registry(*packs)
    return list(raised.value.problems)


def test_a_bad_schema_leaves_the_pack_s_other_problems_reported() -> None:
    bad = replace(
        LIBRARY,
        extension_schemas={"dataset": {"enum": {1, 2}}},
        manifest=manifest(requires_core=">=9.0"),
        concepts=(concept("elsewhere:x"),),
    )
    found = _problems_of(bad)
    assert any("extension schema for dataset is not a JSON value" in p for p in found)
    assert any("requires core" in p for p in found)
    assert any("concept elsewhere:x" in p for p in found)


def test_a_pack_with_a_bad_schema_is_still_registered_for_the_checks_across_packs() -> None:
    bad = replace(LIBRARY, extension_schemas={"dataset": {"maximum": math.nan}})
    citing = replace(ARCHIVE, analyses=(_Fixed(entry("archive.loan_rates")),))
    found = _problems_of(bad, citing)
    assert any("extension schema for dataset" in p for p in found)
    assert not any("which no registered pack declares" in p for p in found)
    twice = _problems_of(bad, LIBRARY)
    assert any("registered twice" in p for p in twice)


def test_every_pack_s_problems_are_collected() -> None:
    first = replace(LIBRARY, extension_schemas={"dataset": {"maximum": math.nan}})
    second = replace(ARCHIVE, extension_schemas={"table": {"enum": {1}}})
    found = _problems_of(first, second)
    assert any(p.startswith("library:") for p in found)
    assert any(p.startswith("archive:") for p in found)


def _deep(depth: int) -> dict[str, Any]:
    schema: dict[str, Any] = {}
    for _ in range(depth):
        schema = {"not": schema}
    return schema


def _cyclic() -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object"}
    schema["not"] = schema
    return schema


@pytest.mark.parametrize(
    ("schema", "problem"),
    [
        ({"maximum": 10**400}, "beyond"),
        ({"maximum": 2.0**60}, "beyond"),
        ({"enum": [math.nan]}, "not finite"),
        ({"properties": {chr(0xDCE9): {}}}, "not Unicode text"),
        (_deep(5_000), "nesting deeper"),
        (_cyclic(), "holds itself"),
        (["type", "object"], "not a JSON object"),
        ("object", "not a JSON object"),
    ],
    ids=["big int", "big float", "nan in a list", "key", "deep", "cyclic", "list", "string"],
)
def test_a_schema_that_json_cannot_carry_is_refused_as_a_problem(schema: Any, problem: str) -> None:
    found = _problems_of(replace(LIBRARY, extension_schemas={"dataset": schema}))
    assert any("extension schema for dataset" in p and problem in p for p in found), found


def test_a_schema_is_copied_down_to_what_its_lists_hold() -> None:
    inner = {"const": 1}
    schema: dict[str, Any] = {"anyOf": [inner]}
    packs = registry(replace(LIBRARY, extension_schemas={"dataset": schema}), ARCHIVE)
    inner["const"] = 2
    assert packs.extension_schemas("dataset", ["library"]) == {"library": {"anyOf": [{"const": 1}]}}


@pytest.mark.parametrize("wording", [["Few"], "", "Few " + chr(0xD800), 5], ids=repr)
def test_a_wording_is_unicode_text(wording: Any) -> None:
    pack = replace(LIBRARY, wording={CaveatCode.SMALL_N: wording})
    assert any("wording for SMALL_N" in p for p in _problems_of(pack))


def test_a_schema_nests_as_deep_as_json_text_may() -> None:
    """64 objects deep registers, 65 do not, as for any JSON value (§14)."""
    assert registry(replace(LIBRARY, extension_schemas={"dataset": _deep(63)}), ARCHIVE)
    found = _problems_of(replace(LIBRARY, extension_schemas={"dataset": _deep(64)}))
    assert any("nesting deeper" in p for p in found), found


def test_a_schema_holds_every_number_json_text_carries() -> None:
    edges = {"minimum": -(2**53 - 1), "maximum": 2**53 - 1, "multipleOf": 0.5}
    packs = registry(replace(LIBRARY, extension_schemas={"dataset": edges}), ARCHIVE)
    assert packs.extension_schemas("dataset", ["library"]) == {"library": edges}


class _Tiny(int):
    def __abs__(self) -> int:
        return 0


class _Ascii(str):
    def isascii(self) -> bool:
        return True


class _Apart(str):
    def __hash__(self) -> int:
        return 0

    def __eq__(self, other: object) -> bool:
        return self is other


class _Small(float):
    def __abs__(self) -> float:
        return 0.0


@pytest.mark.parametrize(
    ("schema", "problem"),
    [
        ({"maximum": _Tiny(2**60)}, "beyond"),
        ({"maximum": _Small(2.0**60)}, "beyond"),
        ({"title": _Ascii(chr(0xD800))}, "not Unicode"),
        ({_Ascii(chr(0xD800)): {}}, "not Unicode text"),
        ({_Apart("maximum"): 1, "maximum": 2}, "two keys written as 'maximum'"),
    ],
    ids=["int subclass", "float subclass", "str subclass", "str subclass key", "colliding keys"],
)
def test_a_schema_is_checked_as_the_plain_values_it_holds(schema: Any, problem: str) -> None:
    """A subclass is read as the value it holds, not as it says it is."""
    found = _problems_of(replace(LIBRARY, extension_schemas={"dataset": schema}))
    assert any(problem in p for p in found), found


def test_a_schema_s_scalars_are_kept_as_plain_values() -> None:
    schema = {"title": _Ascii("Plain"), "maximum": _Tiny(5)}
    packs = registry(replace(LIBRARY, extension_schemas={"dataset": schema}), ARCHIVE)
    kept = cast(dict[str, Any], packs.extension_schemas("dataset", ["library"])["library"])
    assert [type(kept["title"]), type(kept["maximum"])] == [str, int]


def test_a_dropped_schema_or_wording_still_has_its_other_problem_reported() -> None:
    pack = replace(
        LIBRARY,
        extension_schemas={"not_a_kind": {"x": math.nan}},
        wording=cast(Any, {"NOT_A_CODE": 5}),
    )
    found = _problems_of(pack)
    assert any("not_a_kind, which is not a release descriptor kind" in p for p in found), found
    assert any("not a JSON value" in p for p in found), found
    assert any("NOT_A_CODE, which is not a core caveat code" in p for p in found), found
    assert any("wording for NOT_A_CODE is not Unicode text" in p for p in found), found


def test_an_ontology_system_name_is_unicode_text() -> None:
    pack = replace(LIBRARY, ontology_systems={"a" + chr(0xDCE9): lambda code: True})
    assert any("not Unicode text" in p for p in _problems_of(pack))


class _Shaped:
    """A leaf kind whose schema is whatever it is given, which it may change later."""

    def __init__(self, schema: Any) -> None:
        self.given = schema

    @property
    def schema(self) -> Any:
        return self.given

    def compile(self, leaf: PackLeaf, release: ReleaseView, pack_version: str) -> Sequence[Clause]:
        return []

    def summary(self, leaf: PackLeaf) -> Sequence[Segment]:
        return []


def test_leaf_kind_schemas_are_checked_and_kept_as_they_were_when_registered() -> None:
    shaped = _Shaped({"type": "object", "properties": {"worst": {"type": "integer"}}})
    pack = Pack(manifest=manifest(), leaf_kinds={"library.shaped": shaped, **LIBRARY.leaf_kinds})
    packs = registry(pack)
    assert packs.leaf_kinds() == ["library.overdue", "library.shaped"]
    shaped.given = {"type": "string"}
    checker = packs.leaf_checker("library.shaped")
    assert checker.failures({"kind": "library.shaped", "worst": 3}) == []
    assert [f.keyword for f in checker.failures({"kind": "library.shaped", "worst": "x"})] == [
        "type"
    ]


@pytest.mark.parametrize(
    ("schema", "problem"),
    [
        ({"type": "string", "pattern": "^a"}, "is refused"),
        ({"type": "object", "maxProperties": math.inf}, "not a JSON value"),
        (["not", "an", "object"], "not a JSON object"),
    ],
)
def test_a_leaf_kind_s_schema_is_refused_as_an_extension_schema_would_be(
    schema: Any, problem: str
) -> None:
    pack = Pack(manifest=manifest(), leaf_kinds={"library.shaped": _Shaped(schema)})
    with pytest.raises(PackError, match=problem):
        registry(pack)


def test_evaluations_that_share_a_step_budget_spend_it_together() -> None:
    checker = Checker({"type": "array", "items": {"type": "integer"}})
    budget = StepBudget(60)
    assert checker.failures(list(range(20)), budget=budget) == []
    left = budget.left
    assert 0 < left < 60
    assert [f.keyword for f in checker.failures(list(range(200)), budget=budget)] == [OUT_OF_STEPS]

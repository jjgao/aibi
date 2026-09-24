"""Result envelopes, cohort counts and statistic references (SPEC §8.1): every invariant holds."""

import contextlib
import copy
import json
import math
import signal
import warnings
from collections.abc import Callable, Iterator
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum
from typing import Any

import jsonschema
import pytest
from pydantic import BaseModel, ValidationError, computed_field
from pydantic_core import PydanticSerializationError

from aibi.core.schema.export import OUTPUT_SCHEMAS, SCHEMAS
from aibi.core.schema.results import (
    CohortCount,
    Derivation,
    Issuance,
    Population,
    ReleaseRef,
    ResultEnvelope,
    Source,
    Values,
    parse_stat_reference,
    stat_reference,
)
from aibi.core.schema.semantics import ExclusionReason, Reason

HEX = "0123456789abcdef" * 4
LEAF = "leaf:" + HEX
ISSUANCE = "iss:01J8Z3S4T5V6W7X8Y9ZABCDEFG"
MANIFEST = "sha256:" + HEX


def release(**members: Any) -> dict[str, Any]:
    base = {"dataset": "lending", "label": 3, "manifest": MANIFEST, "status": "published"}
    return {**base, **members}


def derivation(**members: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "drv:" + HEX,
        "document": {"aibi": "1", "unit": "members"},
        "analysis": {"id": "survival.km", "version": "1.0.0"},
        "releases": [release()],
        "packs": {"testpack": {"version": "1.2.0", "results_version": 1}},
        "semantics_version": 1,
        "disclosure": {"min_cell_count": None},
        "engine": "aibi 0.1.0",
    }
    return {**base, **members}


def population(**members: Any) -> dict[str, Any]:
    by_reason = {reason.value: 0 for reason in Reason}
    by_reason["NOT_ASSESSED"] = 2
    base: dict[str, Any] = {
        "n_true": 10,
        "n_false": 5,
        "n_unknown": 2,
        "unknown_by_reason": by_reason,
        "unknown_by_leaf": {LEAF: 2},
        "lift_differs": 0,
    }
    return {**base, **members}


def known_population(**members: Any) -> dict[str, Any]:
    """A population without unknown units."""
    zeros = {reason.value: 0 for reason in Reason}
    return population(
        **{"n_unknown": 0, "unknown_by_reason": zeros, "unknown_by_leaf": {LEAF: 0}, **members}
    )


def excluded(**counts: int) -> dict[str, int]:
    """Every exclusion reason, zeros included."""
    return {reason.value: counts.get(reason.value, 0) for reason in ExclusionReason}


def analysed(**members: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"n": 10, "excluded": excluded(), "excluded_units": 0}
    return {**base, **members}


DOCUMENT = {
    "aibi": "1",
    "dataset": "lending",
    "unit": "members",
    "cohorts": {"a": {"all": [{"kind": "value", "column": "members.joined", "range": {"gt": 1}}]}},
}
UNKNOWN_EXCLUDED = {
    "code": "UNKNOWN_EXCLUDED",
    "severity": "warn",
    "message": [{"text": "unknowns excluded"}],
    "affects": ["/analysed", "/population"],
}


def envelope(**members: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "derivation": derivation(),
        "issuance": {"id": ISSUANCE, "cache_hit": False, "values_from": ISSUANCE},
        "source": {"document": DOCUMENT, "leaves": {"/cohorts/a/all/0": [LEAF]}, "params": {}},
        "digest": MANIFEST,
        "cohorts": [
            {"position": 0, "id": "drv:" + HEX, "reference": True},
            {"position": 1, "id": "drv:" + HEX[::-1], "reference": False},
        ],
        "population": [known_population(), known_population()],
        "analysed": [analysed(), analysed()],
        "values": {
            "positions": [{"median": {"estimate": 12.5}}, {"median": {"estimate": 9.0}}],
            "view": {"hazard_ratio": {"estimate": 0.8}},
        },
        "caveats": [],
        "readback": {"cohorts": [[{"text": "a"}], [{"text": "b"}]], "view": [{"text": "a vs b"}]},
        "labels": [{"data": "early"}, {"data": "late"}],
        "charts": [],
    }
    return {**base, **members}


def caveat(code: str, affects: list[str], severity: str = "info") -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": [{"text": "x"}], "affects": affects}


def error_types(model: type[Any], value: dict[str, Any]) -> list[str]:
    with pytest.raises(ValidationError) as raised:
        model.model_validate(value)
    return [str(error["type"]) for error in raised.value.errors()]


# --- The envelope ----------------------------------------------------------------------------


def test_a_result_validates_and_round_trips() -> None:
    result = ResultEnvelope.model_validate(envelope())
    dumped = result.model_dump(mode="json")
    assert ResultEnvelope.model_validate(dumped) == result
    assert ResultEnvelope.model_validate_json(result.model_dump_json()) == result
    jsonschema.validate(dumped, SCHEMAS["result.schema.json"]())


@pytest.mark.parametrize(
    ("members", "error"),
    [
        (
            {"cohorts": [{"position": 1, "id": "drv:" + HEX, "reference": False}] * 2},
            "result_positions",
        ),
        ({"labels": [{"data": "early"}]}, "result_positions"),
        (
            {
                "cohorts": [
                    {"position": 0, "id": "drv:" + HEX, "reference": True},
                    {"position": 1, "id": "drv:" + HEX, "reference": True},
                ]
            },
            "result_reference",
        ),
        ({"analysed": [analysed(n=7), analysed()]}, "result_analysed"),
        ({"caveats": [caveat("SMALL_N", ["/values/positions/5"], "warn")]}, "caveat_affects"),
        ({"caveats": [caveat("NOT_ESTIMABLE", ["/values/view"])]}, "caveat_unfounded"),
        ({"caveats": [caveat("LIFT_DIFFERS", ["/population/0"])]}, "caveat_unfounded"),
        (
            {"caveats": [caveat("DRAFT_RELEASE", ["/derivation/releases/0"], "warn")]},
            "caveat_unfounded",
        ),
    ],
)
def test_result_invariants(members: dict[str, Any], error: str) -> None:
    assert error in error_types(ResultEnvelope, envelope(**members))


def test_suppression_needs_its_caveat() -> None:
    # n_unknown is suppressed, and n_false with it, or the unit table's size would show it.
    suppressed = known_population(
        n_false=None, n_unknown=None, unknown_by_reason=None, unknown_by_leaf=None
    )
    suppressed["suppressed"] = ["/n_false", "/n_unknown", "/unknown_by_leaf", "/unknown_by_reason"]
    value = envelope(
        population=[suppressed, known_population()],
        derivation=derivation(disclosure={"min_cell_count": 5}),
    )
    assert "caveat_missing" in error_types(ResultEnvelope, value)
    value["caveats"] = [caveat("SUPPRESSED", ["/population/0/n_unknown"])]
    # A suppressed n_unknown is never 0, so the units it counts were excluded as unknown.
    assert "caveat_missing" in error_types(ResultEnvelope, value)
    value["caveats"].append(caveat("UNKNOWN_EXCLUDED", ["/population/0"], "warn"))
    assert ResultEnvelope.model_validate(value)


def test_numbers_not_estimable_need_their_caveat() -> None:
    values = {
        "positions": [{"median": {"estimate": None, "not_estimable": {"/estimate": "not_reached"}}}]
        * 2,
        "view": {},
    }
    value = envelope(values=values)
    assert "caveat_missing" in error_types(ResultEnvelope, value)
    value["caveats"] = [caveat("NOT_ESTIMABLE", ["/values/positions/0/median"])]
    assert ResultEnvelope.model_validate(value)
    bad = {
        "positions": [{"median": {"estimate": 1.0, "not_estimable": {"/estimate": "no_units"}}}] * 2
    }
    assert "values_not_estimable" in error_types(
        ResultEnvelope, envelope(values={**bad, "view": {}})
    )


def test_lift_differs_and_draft_releases_need_their_caveats() -> None:
    value = envelope(population=[known_population(lift_differs=3), known_population()])
    assert "caveat_missing" in error_types(ResultEnvelope, value)
    value["caveats"] = [caveat("LIFT_DIFFERS", ["/population/0/lift_differs"])]
    assert ResultEnvelope.model_validate(value)
    draft = envelope(derivation=derivation(releases=[release(label="draft", status="draft")]))
    assert "caveat_missing" in error_types(ResultEnvelope, draft)
    draft["caveats"] = [caveat("DRAFT_RELEASE", ["/derivation/releases/0"], "warn")]
    assert ResultEnvelope.model_validate(draft)


def test_caveats_are_sorted_and_deduplicated() -> None:
    value = envelope(
        population=[known_population(lift_differs=3), known_population(lift_differs=1)]
    )
    first = caveat("LIFT_DIFFERS", ["/population/1/lift_differs"])
    second = caveat("LIFT_DIFFERS", ["/population/0/lift_differs"])
    value["caveats"] = [first, second, first]
    result = ResultEnvelope.model_validate(value)
    assert [c.affects for c in result.caveats] == [
        ["/population/0/lift_differs"],
        ["/population/1/lift_differs"],
    ]


def test_values_are_finite_json() -> None:
    for bad in (math.nan, math.inf, 2**60):
        values = {"positions": [{"x": bad}, {"x": 1}], "view": {}}
        with pytest.raises(ValidationError):
            ResultEnvelope.model_validate(envelope(values=values))


def test_smuggled_non_finite_values_cannot_be_dumped() -> None:
    result = ResultEnvelope.model_validate(envelope())
    with pytest.raises(ValidationError):
        result.values.model_copy(update={"view": {"x": math.nan}})
    values = Values.model_construct(positions=result.values.positions, view={"x": math.nan})
    smuggled = ResultEnvelope.model_construct(**{**dict(result), "values": values})
    with pytest.raises(PydanticSerializationError):
        smuggled.model_dump_json()


# --- Parts ------------------------------------------------------------------------------------


def test_releases_issuances_and_versions() -> None:
    assert "release_status" in error_types(ReleaseRef, release(label="draft"))
    assert "release_status" in error_types(ReleaseRef, release(status="draft"))
    assert ReleaseRef.model_validate(release(label="draft", status="draft"))
    both = derivation(releases=[release(), release()])
    assert "duplicate_release" in error_types(Derivation, both)
    for version, accepted in (
        ("1.2.0", True),
        ("1.2", True),
        ("01.2", False),
        ("1.2.0-rc1", False),
    ):
        packs = {"testpack": {"version": version, "results_version": 1}}
        if accepted:
            assert Derivation.model_validate(derivation(packs=packs))
        else:
            assert error_types(Derivation, derivation(packs=packs))
    packs = {"testpack": {"version": "01.2", "results_version": 1}}
    assert "version" in error_types(Derivation, derivation(packs=packs))
    reserved = {"summary": {"version": "1.0", "results_version": 1}}
    assert error_types(Derivation, derivation(packs=reserved))
    other = "iss:01J8Z3S4T5V6W7X8Y9ZABCDEFH"
    assert Issuance.model_validate({"id": ISSUANCE, "cache_hit": True, "values_from": other})
    for cache_hit, values_from in ((True, ISSUANCE), (False, other)):
        issuance = {"id": ISSUANCE, "cache_hit": cache_hit, "values_from": values_from}
        assert "issuance" in error_types(Issuance, issuance)


@pytest.mark.parametrize(
    ("members", "error"),
    [
        ({"n_true": None}, "population_suppressed"),
        ({"n_true": None, "suppressed": ["/n_true", "/n_false"]}, "population_suppressed"),
        ({"n_unknown": None, "suppressed": ["/n_unknown"]}, "population_breakdown"),
        (
            {"n_unknown": None, "unknown_by_reason": None, "suppressed": ["/n_unknown"]},
            "population_suppressed",
        ),
        ({"unknown_by_reason": {"NOT_ASSESSED": 2}}, "population_reasons"),
        ({"unknown_by_leaf": {LEAF: 3}}, "population_breakdown"),
        (
            {"unknown_by_reason": {**{r.value: 0 for r in Reason}, "NO_ROWS": 1}},
            "population_reasons",
        ),
        ({"lift_differs": 18}, "population_lift"),
    ],
)
def test_population_rules(members: dict[str, Any], error: str) -> None:
    assert error in error_types(Population, population(**members))


def test_a_suppressed_count_is_listed_by_its_pointer() -> None:
    suppressed = Population.model_validate(population(n_false=None, suppressed=["/n_false"]))
    assert suppressed.suppressed == ["/n_false"]
    assert suppressed.model_dump()["n_false"] is None


def test_analysed_counts() -> None:
    from aibi.core.schema.results import Analysed

    assert Analysed.model_validate(analysed())
    assert "analysed_excluded" in error_types(Analysed, analysed(excluded_units=5))
    assert "analysed_excluded" in error_types(
        Analysed, analysed(excluded={"NOT_ASSESSED": 3}, excluded_units=2)
    )
    hidden = analysed(n=None, not_estimable={"/n": "suppressed"})
    assert Analysed.model_validate(hidden)
    wrong = analysed(n=None, not_estimable={"/n": "no_units"})
    assert "analysed_suppressed" in error_types(Analysed, wrong)
    assert "analysed_breakdown" in error_types(
        Analysed,
        analysed(excluded_units=None, not_estimable={"/excluded_units": "suppressed"}),
    )


# --- Cohort counts ----------------------------------------------------------------------------


def cohort_count(**members: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "drv:" + HEX,
        "digest": MANIFEST,
        "population": known_population(),
        "size": {
            "estimate": 10 / 15,
            "numerator": 10,
            "denominator": 15,
            "denominator_definition": {"position": None, "predicate": None, "counts": "unit_table"},
        },
        "disclosure": {"min_cell_count": None},
        "readback": [{"text": "members who borrowed"}],
        "caveats": [],
        "releases": [release()],
        "issuance": {"id": ISSUANCE, "cache_hit": False, "values_from": ISSUANCE},
    }
    return {**base, **members}


def test_a_cohort_count_validates_and_matches_its_schema() -> None:
    count = CohortCount.model_validate(cohort_count())
    jsonschema.validate(count.model_dump(mode="json"), SCHEMAS["cohort-count.schema.json"]())


def test_cohort_count_rules() -> None:
    size = cohort_count()["size"]
    wrong_definition = {
        **size,
        "denominator_definition": {**size["denominator_definition"], "counts": "known"},
    }
    assert "count_size" in error_types(CohortCount, cohort_count(size=wrong_definition))
    assert "count_size" in error_types(
        CohortCount, cohort_count(size={**size, "numerator": 9, "estimate": 9 / 15})
    )
    assert "count_size" in error_types(
        CohortCount, cohort_count(size={**size, "denominator": 18, "estimate": 10 / 18})
    )
    lifted = cohort_count(population=known_population(lift_differs=1))
    assert "caveat_missing" in error_types(CohortCount, lifted)
    lifted["caveats"] = [caveat("LIFT_DIFFERS", ["/population/lift_differs"])]
    assert "count_affects" in error_types(CohortCount, lifted)
    lifted["caveats"] = [caveat("LIFT_DIFFERS", ["/population"])]
    assert CohortCount.model_validate(lifted)


def test_a_suppressed_cohort_count() -> None:
    # n_false is suppressed with n_true, or the unit table's size would show n_true.
    hidden = known_population(n_true=None, n_false=None, suppressed=["/n_false", "/n_true"])
    size = {
        "estimate": None,
        "numerator": None,
        "denominator": 17,
        "denominator_definition": {"position": None, "predicate": None, "counts": "unit_table"},
        "not_estimable": {"/estimate": "suppressed", "/numerator": "suppressed"},
    }
    count = cohort_count(population=hidden, size=size, disclosure={"min_cell_count": 5})
    assert "caveat_missing" in error_types(CohortCount, count)
    count["caveats"] = [caveat("SUPPRESSED", ["/population"])]
    assert CohortCount.model_validate(count)
    off = {**count, "disclosure": {"min_cell_count": None}}
    assert "disclosure" in error_types(CohortCount, off)


# --- Statistic references ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("descriptor", "pointer", "floor"),
    [
        ("members.age_days", "/histogram", None),
        ("dataset", "", 10),
        ("rel:loans.borrower", "/by~1key/a b%", None),
        ("members", "/n_rows", 2),
    ],
)
def test_statistic_references_round_trip(descriptor: str, pointer: str, floor: int | None) -> None:
    reference = stat_reference(MANIFEST, descriptor, pointer, floor)
    parsed = parse_stat_reference(reference)
    assert (parsed.manifest, parsed.descriptor_id, parsed.pointer, parsed.floor) == (
        MANIFEST,
        descriptor,
        pointer,
        floor,
    )


def test_statistic_references_refuse_other_forms() -> None:
    for bad in ("stat:sha256:abc/dataset", "stat:" + MANIFEST + "/concept:x", "stat:" + MANIFEST):
        with pytest.raises(ValueError, match="not a statistic reference"):
            parse_stat_reference(bad)
    with pytest.raises(ValueError, match="a floor is from 2"):
        stat_reference(MANIFEST, "dataset", "", floor=1)


# --- The output schemas -----------------------------------------------------------------------


def _free(schema: dict[str, Any]) -> list[str]:
    """Where an output's schema lets text through that is not marked as data (A6).

    It follows ``$ref`` from the root, carrying whether a node or one of its ancestors is
    marked. Free text is a string without a pattern, enum, const or format; any value (``{}``);
    and an object whose keys are not fixed. The server's own text in text segments is exempt.
    """
    defs: dict[str, Any] = schema.get("$defs", {})
    found: list[str] = []
    seen: set[tuple[str, bool]] = set()

    def visit(node: Any, where: str, marked: bool) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                visit(item, f"{where}/{index}", marked)
            return
        if not isinstance(node, dict):
            return
        marked = marked or node.get("x-aibi-data") is True
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.removeprefix("#/$defs/")
            if (name, marked) not in seen:
                seen.add((name, marked))
                visit(defs[name], f"{where}->{name}", marked)
        keys = set(node) - {"description", "title", "default", "x-aibi-data", "x-aibi-computed"}
        types = node.get("type")
        texts = types == "string" or (isinstance(types, list) and "string" in types)
        exempt = where.endswith("->TextSegment/properties/text")
        if not marked and not exempt:
            if texts and not {"pattern", "enum", "const", "format"} & node.keys():
                found.append(where)
            elif not keys:
                found.append(where + " (any value)")
            elif (
                types == "object"
                and node.get("additionalProperties") not in (False, None)
                and "propertyNames" not in node
            ):
                found.append(where + " (free keys)")
        for key, value in node.items():
            if key not in ("$ref", "$defs"):
                visit(value, f"{where}/{key}", marked)

    visit(schema, "", False)
    return found


@pytest.mark.parametrize("name", sorted(OUTPUT_SCHEMAS))
def test_free_text_in_outputs_is_marked_as_data(name: str) -> None:
    """A string the server did not write is data (A6): free text is marked, except the server's
    own text in text segments."""
    assert _free(SCHEMAS[name]()) == []


def test_the_walker_finds_unmarked_text() -> None:
    schema = {
        "properties": {
            "a": {"type": "string"},
            "b": {"$ref": "#/$defs/B"},
            "c": {"$ref": "#/$defs/B", "x-aibi-data": True},
            "d": {},
        },
        "$defs": {"B": {"type": "object", "additionalProperties": {"type": "integer"}}},
    }
    assert _free(schema) == [
        "/properties/a",
        "/properties/b->B (free keys)",
        "/properties/d (any value)",
    ]


def test_outputs_dumped_to_json_validate_again() -> None:
    result = ResultEnvelope.model_validate(envelope())
    again = json.loads(result.model_dump_json())
    assert ResultEnvelope.model_validate(copy.deepcopy(again)) == result


# --- Round 1: what raises caveats, JSON safety, disclosure and the parts ---------------------


def test_caveats_are_raised_only_by_the_digested_parts() -> None:
    # A cohort named not_estimable, and such maps in documents, parameters and charts, are text.
    marks = {"not_estimable": {"/a": "suppressed"}}
    document = {**DOCUMENT, "cohorts": {"not_estimable": {"all": []}}, "params": marks}
    source = {"document": document, "leaves": {}, "params": marks}
    value = envelope(
        source=source,
        derivation=derivation(document={"not_estimable": {"/x": "no_units"}}),
        charts=[{"data": {"values": [marks]}}],
    )
    result = ResultEnvelope.model_validate(value)
    assert result.caveats == []


@pytest.mark.parametrize(
    "members",
    [
        {"derivation": derivation(document={"x": math.nan})},
        {"derivation": derivation(document={"x": 2**60})},
        {"source": {"document": {"x": "caf\udce9"}, "leaves": {}, "params": {}}},
        {"source": {"document": DOCUMENT, "leaves": {}, "params": {"p": math.inf}}},
        {"charts": [{"x": 1e20}]},
        {"values": {"positions": [{"x": 1.5e300}, {}], "view": {}}},
    ],
)
def test_every_json_member_holds_only_what_json_carries(members: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ResultEnvelope.model_validate(envelope(**members))


@pytest.mark.parametrize(
    "bad",
    [math.nan, 2**60, "caf\udce9", Decimal("NaN"), Decimal("1E+20"), {1, 2}, 1j, {1: 2}],
    ids=repr,
)
def test_smuggled_values_cannot_be_dumped_anywhere(bad: object) -> None:
    result = ResultEnvelope.model_validate(envelope())
    with pytest.raises(ValidationError):
        result.source.model_copy(update={"params": {"p": bad}})
    source = Source.model_construct(**{**dict(result.source), "params": {"p": bad}})
    smuggled = ResultEnvelope.model_construct(**{**dict(result), "source": source})
    dumps: list[Callable[[], object]] = [
        smuggled.model_dump,
        lambda: smuggled.model_dump(mode="json"),
        smuggled.model_dump_json,
    ]
    for dump in dumps:
        # Pydantic's own serializer only warns about some of these; the check before the dump
        # refuses them all.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(PydanticSerializationError, match=r"Outputs (hold|have|contain)"):
                dump()
    # Nested outputs built without validation are validated where they are used.
    with pytest.raises(ValidationError):
        ResultEnvelope.model_validate({**dict(result), "source": source})


@pytest.mark.parametrize(
    ("members", "error"),
    [
        ({"cohorts": []}, "too_short"),
        (
            {
                "cohorts": [
                    {"position": i, "id": "drv:" + HEX, "reference": False} for i in range(7)
                ]
            },
            "too_long",
        ),
        (
            {
                "issuance": {
                    "id": ISSUANCE,
                    "cache_hit": True,
                    "values_from": "iss:01J8Z3S4T5V6W7X8Y9ZABCDEFH",
                }
            },
            "draft_cached",
        ),
        ({"caveats": [caveat("SMALL_N", ["/digest"], "warn")]}, "caveat_affects"),
        ({"caveats": [caveat("SMALL_N", [""], "warn")]}, "caveat_affects"),
        ({"caveats": [caveat("SMALL_N", ["/source/params"], "warn")]}, "caveat_affects"),
        ({"caveats": [caveat("SMALL_N", ["/derivation/releases/0"], "warn")]}, "caveat_affects"),
        (
            {
                "source": {
                    "document": DOCUMENT,
                    "leaves": {"/cohorts/zzz/all/9": [LEAF]},
                    "params": {},
                }
            },
            "source_leaves",
        ),
        (
            {"source": {"document": DOCUMENT, "leaves": {"/cohorts/a/all/0": []}, "params": {}}},
            "too_short",
        ),
    ],
)
def test_more_result_invariants(members: dict[str, Any], error: str) -> None:
    if "issuance" in members:
        members["derivation"] = derivation(releases=[release(label="draft", status="draft")])
        members["caveats"] = [caveat("DRAFT_RELEASE", ["/derivation/releases/0"], "warn")]
    assert error in error_types(ResultEnvelope, envelope(**members))


def test_releases_are_one_per_dataset_and_sorted() -> None:
    other = release(dataset="archive", manifest="sha256:" + HEX[::-1])
    releases = Derivation.model_validate(derivation(releases=[other, release()])).releases
    assert [r.dataset for r in releases] == ["archive", "lending"]
    # Refused out of order, not sorted: caveats point at releases by index.
    assert "release_order" in error_types(Derivation, derivation(releases=[release(), other]))
    twice = derivation(releases=[release(), release(manifest="sha256:" + HEX[::-1])])
    assert "duplicate_release" in error_types(Derivation, twice)


@pytest.mark.parametrize(
    "model_and_value",
    [
        (ReleaseRef, release(label=2**60)),
        (Derivation, derivation(semantics_version=2**60)),
        (Derivation, derivation(disclosure={"min_cell_count": 2**60})),
        (Derivation, derivation(packs={"testpack": {"version": "1.0", "results_version": 2**60}})),
        (Derivation, derivation(engine="aibi " + "1" * 65)),
    ],
)
def test_integers_and_names_are_bounded(model_and_value: tuple[type[Any], dict[str, Any]]) -> None:
    model, value = model_and_value
    assert error_types(model, value)


def test_unknown_units_and_invalid_rows_need_their_caveats() -> None:
    value = envelope(population=[population(), population()])
    value["analysed"] = [analysed(n=8, excluded=excluded(NOT_ASSESSED=2), excluded_units=2)] * 2
    assert "caveat_missing" in error_types(ResultEnvelope, value)
    value["caveats"] = [UNKNOWN_EXCLUDED]
    assert ResultEnvelope.model_validate(value)
    invalid = envelope(
        analysed=[analysed(n=8, excluded=excluded(INVALID_VALUE=2), excluded_units=2)] * 2
    )
    assert "caveat_missing" in error_types(ResultEnvelope, invalid)
    invalid["caveats"] = [caveat("INVALID_EXCLUDED", ["/analysed"], "warn")]
    assert ResultEnvelope.model_validate(invalid)


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("overlapping_cohorts", "COHORTS_OVERLAP"),
        ("confounded_with_dataset", "CONFOUNDED_WITH_DATASET"),
    ],
)
def test_reasons_of_views_need_their_caveats(reason: str, code: str) -> None:
    view = {"hazard_ratio": {"estimate": None, "not_estimable": {"/estimate": reason}}}
    value = envelope(values={"positions": [{}, {}], "view": view})
    value["caveats"] = [caveat("NOT_ESTIMABLE", ["/values/view"])]
    assert "caveat_missing" in error_types(ResultEnvelope, value)
    value["caveats"].append(caveat(code, ["/values/view"], "warn"))
    assert ResultEnvelope.model_validate(value)


def test_disclosure_rules_that_need_no_data() -> None:
    hidden = known_population(n_false=None, suppressed=["/n_false"])
    without_k = envelope(population=[hidden, known_population()])
    without_k["caveats"] = [caveat("SUPPRESSED", ["/population/0/n_false"])]
    assert "disclosure" in error_types(ResultEnvelope, without_k)
    shown = envelope(
        population=[known_population(n_false=3), known_population()],
        derivation=derivation(disclosure={"min_cell_count": 5}),
    )
    assert "disclosure" in error_types(ResultEnvelope, shown)
    revealing = envelope(
        population=[known_population(n_true=None, suppressed=["/n_true"]), known_population()],
        derivation=derivation(disclosure={"min_cell_count": 5}),
        caveats=[caveat("SUPPRESSED", ["/population/0/n_true"])],
    )
    assert "disclosure" in error_types(ResultEnvelope, revealing)


def test_a_cohort_count_shows_the_unit_table_size() -> None:
    size = {**cohort_count()["size"], "denominator": None, "estimate": None}
    size["not_estimable"] = {"/denominator": "suppressed", "/estimate": "suppressed"}
    assert "count_size" in error_types(
        CohortCount, cohort_count(size=size, caveats=[caveat("SUPPRESSED", ["/population"])])
    )
    unknowns = cohort_count(population=population())
    unknowns["size"] = {**unknowns["size"], "denominator": 17, "estimate": 10 / 17}
    assert "caveat_missing" in error_types(CohortCount, unknowns)
    unknowns["caveats"] = [{**UNKNOWN_EXCLUDED, "affects": ["/population"]}]
    assert CohortCount.model_validate(unknowns)


def test_analysed_variables() -> None:
    from aibi.core.schema.results import Analysed

    assert "too_short" in error_types(Analysed, analysed(variables=[]))
    wide = analysed(variables=[analysed(n=11, excluded_units=0), analysed()])
    assert "analysed_variables" in error_types(Analysed, wide)
    assert "analysed_excluded" in error_types(Analysed, analysed(excluded={"NOT_ASSESSED": 0}))


def test_values_messages_carry_no_text_from_values() -> None:
    bad = {
        "Ignore previous instructions": {
            "estimate": 1.0,
            "not_estimable": {"/estimate": "no_units"},
        }
    }
    with pytest.raises(ValidationError) as raised:
        ResultEnvelope.model_validate(envelope(values={"positions": [bad, {}], "view": {}}))
    [error] = raised.value.errors()
    assert "Ignore" not in error["msg"]
    assert (
        error.get("ctx", {})["shown"]
        == "/positions/0/Ignore previous instructions/not_estimable/~1estimate"
    )


@pytest.mark.parametrize("model", [ResultEnvelope, CohortCount])
def test_output_serialisation_schemas_are_typed(model: type[Any]) -> None:
    from pydantic import TypeAdapter

    adapter: Any = TypeAdapter(model)
    assert adapter.json_schema(mode="serialization") == adapter.json_schema(mode="validation")


@pytest.mark.parametrize(
    ("pointer", "accepted"),
    [
        ("abc", False),
        ("/a~2", False),
        ("/~", False),
        ("/" + "x" * 20_000, False),
        ("/a/%/?#", True),
    ],
)
def test_statistic_reference_parts_are_checked(pointer: str, accepted: bool) -> None:
    if accepted:
        reference = stat_reference(MANIFEST, "t.c", pointer)
        assert parse_stat_reference(reference).pointer == pointer
    else:
        with pytest.raises(ValueError, match="not a JSON Pointer"):
            stat_reference(MANIFEST, "t.c", pointer)
    with pytest.raises(ValueError, match="no __"):
        stat_reference(MANIFEST, "a__b", "/x")


@pytest.mark.parametrize(
    "reference",
    [
        "stat:" + MANIFEST + "/t%62",
        "stat:" + MANIFEST + "/t/%7e",
        "stat:" + MANIFEST + "/t/%2F",
        "stat:" + MANIFEST + "/t/a b",
        "stat:" + MANIFEST + "/t/%FF",
        "stat:" + MANIFEST + "/t?floor=1",
        "stat:" + MANIFEST + "/t?floor=" + "9" * 30,
    ],
)
def test_statistic_references_have_one_spelling(reference: str) -> None:
    with pytest.raises(ValueError, match=r"statistic reference|percent-encoded|a floor|Pointer"):
        parse_stat_reference(reference)


def _schema_refuses(name: str, value: Any) -> bool:
    return not jsonschema.Draft202012Validator(SCHEMAS[name]()).is_valid(value)


def test_the_output_schemas_say_what_json_schema_can() -> None:
    result = ResultEnvelope.model_validate(envelope()).model_dump(mode="json")
    assert not _schema_refuses("result.schema.json", result)
    wrong_severity = {**result, "caveats": [caveat("SMALL_N", ["/values"], "info")]}
    unknown_code = {**result, "caveats": [caveat("NOT_A_CODE", ["/values"], "warn")]}
    two_references = {**result, "cohorts": [{**c, "reference": True} for c in result["cohorts"]]}
    draft_label = copy.deepcopy(result)
    draft_label["derivation"]["releases"][0]["status"] = "draft"
    big = copy.deepcopy(result)
    big["values"]["view"] = {"x": 2**60}
    partial = copy.deepcopy(result)
    partial["analysed"][0]["excluded"] = {"NOT_ASSESSED": 0}
    for value in (wrong_severity, unknown_code, two_references, draft_label, big, partial):
        assert _schema_refuses("result.schema.json", value)
    count = CohortCount.model_validate(cohort_count()).model_dump(mode="json")
    assert not _schema_refuses("cohort-count.schema.json", count)
    elsewhere = {**count, "caveats": [caveat("SMALL_N", ["/size"], "warn")]}
    known = copy.deepcopy(count)
    known["size"]["denominator_definition"]["counts"] = "known"
    for value in (elsewhere, known):
        assert _schema_refuses("cohort-count.schema.json", value)
    manifest = {"id": "rel", "version": "1.0", "results_version": 1, "requires_core": ">=0.1"}
    assert _schema_refuses("pack-manifest.schema.json", manifest)


# --- Round 2: the disclosure rules that need no data, and what JSON Schema can say ----------

K5 = {"min_cell_count": 5}


def _unknowns(**members: Any) -> dict[str, Any]:
    """Six unknown units, enough to show under k = 5."""
    by_reason = {reason.value: 0 for reason in Reason}
    by_reason["NOT_ASSESSED"] = 6
    base: dict[str, Any] = {
        "n_unknown": 6,
        "unknown_by_reason": by_reason,
        "unknown_by_leaf": {LEAF: 6},
    }
    return population(**{**base, **members})


def _under_k(**members: Any) -> dict[str, Any]:
    value = envelope(derivation=derivation(disclosure=K5), **members)
    if any(p["n_unknown"] != 0 for p in value["population"]):
        value["caveats"] = [*value["caveats"], caveat("UNKNOWN_EXCLUDED", ["/population"], "warn")]
    return value


def test_shown_breakdowns_hold_no_count_below_k() -> None:
    assert ResultEnvelope.model_validate(_under_k(population=[_unknowns(), known_population()]))
    by_reason = {**_unknowns()["unknown_by_reason"], "NOT_ASSESSED": 5, "NO_ROWS": 1}
    for changed in (
        _unknowns(unknown_by_reason=by_reason),
        _unknowns(unknown_by_leaf={LEAF: 6, "leaf:" + HEX[::-1]: 2}),
    ):
        value = _under_k(population=[changed, known_population()])
        assert "disclosure" in error_types(ResultEnvelope, value)
    invalid = analysed(n=8, excluded=excluded(INVALID_VALUE=2), excluded_units=2)
    value = _under_k(analysed=[invalid, analysed()])
    value["caveats"].append(caveat("INVALID_EXCLUDED", ["/analysed/0"], "warn"))
    assert "disclosure" in error_types(ResultEnvelope, value)


def test_each_variable_follows_the_count_rules() -> None:
    small = analysed(
        variables=[analysed(n=2, excluded=excluded(NO_PARENT=8), excluded_units=8), analysed()]
    )
    value = _under_k(analysed=[small, analysed()])
    value["caveats"].append(caveat("UNKNOWN_EXCLUDED", ["/analysed/0"], "warn"))
    assert "disclosure" in error_types(ResultEnvelope, value)
    # n_true and n_false suppressed; a variable's n and excluded_units would add up to n_true.
    hidden = known_population(n_true=None, n_false=None, suppressed=["/n_false", "/n_true"])
    overall = analysed(
        n=None,
        excluded=None,
        excluded_units=None,
        not_estimable={
            "/n": "suppressed",
            "/excluded": "suppressed",
            "/excluded_units": "suppressed",
        },
        variables=[analysed(n=7, excluded=excluded(NO_PARENT=5), excluded_units=5)] * 2,
    )
    value = _under_k(population=[hidden, known_population()], analysed=[overall, analysed()])
    value["caveats"] += [
        caveat("SUPPRESSED", ["/population/0"]),
        caveat("UNKNOWN_EXCLUDED", ["/analysed/0"], "warn"),
    ]
    assert "disclosure" in error_types(ResultEnvelope, value)


@pytest.mark.parametrize(
    ("members", "suppressed", "revealing"),
    [
        ({"n_false": None}, ["/n_false"], True),
        ({"n_false": None, "n_true": None}, ["/n_false", "/n_true"], False),
        ({"n_true": None, "n_false": 0}, ["/n_true"], False),
    ],
)
def test_one_suppressed_count_is_not_shown_by_the_others(
    members: dict[str, Any], suppressed: list[str], revealing: bool
) -> None:
    hidden = known_population(**members, suppressed=suppressed)
    n_true = hidden["n_true"]
    first = (
        analysed()
        if n_true is not None
        else analysed(
            n=None,
            excluded=None,
            excluded_units=None,
            not_estimable={
                "/n": "suppressed",
                "/excluded": "suppressed",
                "/excluded_units": "suppressed",
            },
        )
    )
    value = _under_k(population=[hidden, known_population()], analysed=[first, analysed()])
    value["caveats"].append(caveat("SUPPRESSED", ["/population/0"]))
    if revealing:
        assert "disclosure" in error_types(ResultEnvelope, value)
    else:
        assert ResultEnvelope.model_validate(value)


def test_analysed_counts_do_not_show_a_suppressed_one() -> None:
    hidden = analysed(n=None, excluded_units=5, excluded=excluded(NO_PARENT=5))
    hidden["not_estimable"] = {"/n": "suppressed"}
    value = _under_k(analysed=[hidden, analysed()])
    value["population"] = [known_population(n_true=10), known_population()]
    value["caveats"] += [
        caveat("SUPPRESSED", ["/analysed/0"]),
        caveat("UNKNOWN_EXCLUDED", ["/analysed/0"], "warn"),
    ]
    assert "disclosure" in error_types(ResultEnvelope, value)


def test_suppressed_is_carried_only_with_a_min_cell_count() -> None:
    value = envelope(caveats=[caveat("SUPPRESSED", ["/population"])])
    assert "disclosure" in error_types(ResultEnvelope, value)
    count = cohort_count(caveats=[caveat("SUPPRESSED", ["/population"])])
    assert "disclosure" in error_types(CohortCount, count)


def test_cohort_counts_follow_the_disclosure_rules() -> None:
    hidden = known_population(n_true=None, n_false=5, suppressed=["/n_true"])
    size = {
        "estimate": None,
        "numerator": None,
        "denominator": 15,
        "denominator_definition": {"position": None, "predicate": None, "counts": "unit_table"},
        "not_estimable": {"/estimate": "suppressed", "/numerator": "suppressed"},
    }
    count = cohort_count(population=hidden, size=size, disclosure=K5)
    count["caveats"] = [caveat("SUPPRESSED", ["/population"])]
    assert "disclosure" in error_types(CohortCount, count)  # 15 - 5 - 0 shows n_true
    small = cohort_count(population=known_population(n_false=3), disclosure=K5)
    small["size"] = {**small["size"], "denominator": 13, "estimate": 10 / 13}
    assert "disclosure" in error_types(CohortCount, small)
    unknown = cohort_count(
        population=known_population(
            n_false=None,
            n_unknown=None,
            unknown_by_reason=None,
            unknown_by_leaf=None,
            suppressed=["/n_false", "/n_unknown", "/unknown_by_leaf", "/unknown_by_reason"],
        ),
        disclosure=K5,
    )
    unknown["size"] = {**unknown["size"], "denominator": 17, "estimate": 10 / 17}
    unknown["caveats"] = [caveat("SUPPRESSED", ["/population"])]
    assert "caveat_missing" in error_types(CohortCount, unknown)  # n_unknown is not 0
    unknown["caveats"].append({**UNKNOWN_EXCLUDED, "affects": ["/population"]})
    assert CohortCount.model_validate(unknown)


def test_cohort_counts_carry_no_analysis_caveats_or_exclusions() -> None:
    for code in ("COHORTS_OVERLAP", "CONFOUNDED_WITH_DATASET", "INVALID_EXCLUDED"):
        value = cohort_count(caveats=[caveat(code, ["/population"], "warn")])
        assert "count_caveat" in error_types(CohortCount, value)
    size = {**cohort_count()["size"], "excluded": excluded()}
    assert "count_size" in error_types(CohortCount, cohort_count(size=size))


def test_impossible_populations_are_refused() -> None:
    zero = known_population(unknown_by_reason=None, suppressed=["/unknown_by_reason"])
    assert "population_breakdown" in error_types(Population, zero)
    short = population(unknown_by_leaf={LEAF: 1})
    assert "population_leaves" in error_types(Population, short)
    assert Population.model_validate(population(unknown_by_leaf={LEAF: 2, "leaf:" + HEX[::-1]: 1}))
    zero_units = analysed(excluded=None, not_estimable={"/excluded": "suppressed"})
    from aibi.core.schema.results import Analysed

    assert "analysed_breakdown" in error_types(Analysed, zero_units)


def test_not_estimable_maps_with_odd_reasons_are_refused_not_crashed() -> None:
    for reason in ({}, [], 5):
        values = {
            "positions": [{"m": {"x": None, "not_estimable": {"/x": reason}}}, {}],
            "view": {},
        }
        assert "values_not_estimable" in error_types(ResultEnvelope, envelope(values=values))


def test_leaf_pointers_name_a_part_of_the_document() -> None:
    source = {"document": DOCUMENT, "leaves": {"": [LEAF]}, "params": {}}
    assert error_types(ResultEnvelope, envelope(source=source))


def test_caveats_and_limits_hold_what_json_carries() -> None:
    from aibi.core.schema.caveats import Caveat
    from aibi.core.schema.refusals import Limit

    for affects in (["/values/a￾"], ["/values/" + "x" * 16_400]):
        assert error_types(Caveat, caveat("SMALL_N", affects, "warn"))
    for maximum in (2**60, -5):
        assert error_types(Limit, {"name": "values", "max": maximum})


def test_statistic_references_name_real_relationships() -> None:
    columns = "+".join(f"c{index}" for index in range(17))
    with pytest.raises(ValueError, match="16 columns"):
        stat_reference(MANIFEST, f"rel:t.{columns}", "/n_rows")
    with pytest.raises(ValueError, match="Unicode"):
        stat_reference(MANIFEST, "members", "/a￾")


def test_the_schemas_say_more_of_what_the_models_refuse() -> None:
    result = ResultEnvelope.model_validate(envelope()).model_dump(mode="json")
    huge = copy.deepcopy(result)
    huge["values"]["view"] = {"x": 1e300}
    affects = {**result, "caveats": [caveat("SMALL_N", ["/source"], "warn")]}
    releases = {**result, "caveats": [caveat("SMALL_N", ["/derivation/releases/0"], "warn")]}
    positions = copy.deepcopy(result)
    positions["cohorts"][1]["position"] = 5
    cached = copy.deepcopy(result)
    cached["derivation"]["releases"][0].update(status="draft", label="draft")
    cached["issuance"] = {
        **cached["issuance"],
        "cache_hit": True,
        "values_from": ISSUANCE[:-1] + "H",
    }
    unlisted = copy.deepcopy(result)
    unlisted["population"][0]["n_false"] = None
    listed = copy.deepcopy(result)
    listed["population"][0]["suppressed"] = ["/n_false"]
    breakdown = copy.deepcopy(result)
    breakdown["population"][0].update(n_unknown=None, suppressed=["/n_unknown"])
    keyless = copy.deepcopy(result)
    keyless["analysed"][0]["n"] = None
    for value in (huge, affects, releases, positions, cached, unlisted, listed, breakdown, keyless):
        assert _schema_refuses("result.schema.json", value), value
    draft_release = caveat("DRAFT_RELEASE", ["/derivation/releases/0"], "warn")
    fine = copy.deepcopy(cached)
    fine["issuance"] = result["issuance"]
    fine["caveats"] = [draft_release]
    assert not _schema_refuses("result.schema.json", fine)

    count = CohortCount.model_validate(cohort_count()).model_dump(mode="json")
    hidden_denominator = copy.deepcopy(count)
    hidden_denominator["size"].update(denominator=None, estimate=None)
    hidden_denominator["size"]["not_estimable"] = {
        "/denominator": "suppressed",
        "/estimate": "suppressed",
    }
    excluded_size = copy.deepcopy(count)
    excluded_size["size"]["excluded"] = excluded()
    overlap = {**count, "caveats": [caveat("COHORTS_OVERLAP", ["/population"], "warn")]}
    not_suppressed = copy.deepcopy(count)
    not_suppressed["size"].update(numerator=None, estimate=None)
    not_suppressed["size"]["not_estimable"] = {"/numerator": "no_units", "/estimate": "suppressed"}
    unkeyed = copy.deepcopy(count)
    unkeyed["size"]["excluded"] = None
    for value in (hidden_denominator, excluded_size, overlap, not_suppressed, unkeyed):
        assert _schema_refuses("cohort-count.schema.json", value), value

    manifest = {"id": "a__b", "version": "1.0", "results_version": 1, "requires_core": ">=0.1"}
    assert _schema_refuses("pack-manifest.schema.json", manifest)
    for requires in (">=0.1,,<0.2", ">= 0.1"):
        spaced = {**manifest, "id": "library", "requires_core": requires}
        assert _schema_refuses("pack-manifest.schema.json", spaced)


# --- Round 3: the size's complement, the rules each mutant broke, and cycles ----------------


def _count_with(n_true: int | None, n_false: int | None, n_unknown: int | None, size: int) -> Any:
    """A cohort count under k = 5; members given as None are suppressed."""
    members = {"n_true": n_true, "n_false": n_false, "n_unknown": n_unknown}
    hidden = sorted(f"/{name}" for name, value in members.items() if value is None)
    by_reason = {reason.value: 0 for reason in Reason}
    extra: dict[str, Any] = {"unknown_by_reason": by_reason, "unknown_by_leaf": {LEAF: 0}}
    if n_unknown is None:
        extra = {"unknown_by_reason": None, "unknown_by_leaf": None}
        hidden = sorted([*hidden, "/unknown_by_leaf", "/unknown_by_reason"])
    population = known_population(**members, **extra, suppressed=hidden)
    proportion: dict[str, Any] = {
        "estimate": None if n_true is None else n_true / size,
        "numerator": n_true,
        "denominator": size,
        "denominator_definition": {"position": None, "predicate": None, "counts": "unit_table"},
    }
    if n_true is None:
        proportion["not_estimable"] = {"/estimate": "suppressed", "/numerator": "suppressed"}
    count = cohort_count(population=population, size=proportion, disclosure=K5)
    if hidden:
        count["caveats"] = [caveat("SUPPRESSED", ["/population"])]
    if n_unknown != 0:
        count["caveats"] = [*count["caveats"], {**UNKNOWN_EXCLUDED, "affects": ["/population"]}]
    return count


@pytest.mark.parametrize(
    ("parts", "size", "accepted"),
    [
        ((10, None, None), 14, False),  # 4 units outside the cohort show
        ((10, None, None), 12, False),  # and, with 2, each suppressed count is 1
        ((10, None, None), 15, True),
        ((0, None, None), 3, True),  # the numerator is 0: nothing else to suppress
        ((None, None, None), 14, True),  # what the pass writes for the first case
        ((None, 0, 0), 3, True),  # every other member 0 (SPEC §8.4's exception)
    ],
)
def test_a_cohort_count_hides_a_small_complement(
    parts: tuple[int | None, int | None, int | None], size: int, accepted: bool
) -> None:
    count = _count_with(*parts, size=size)
    if accepted:
        assert CohortCount.model_validate(count)
    else:
        assert "disclosure" in error_types(CohortCount, count)


def test_counts_at_and_over_k_minus_one() -> None:
    for n_false, accepted in ((4, False), (5, True), (0, True), (1, False)):
        value = envelope(
            population=[known_population(n_false=n_false), known_population()],
            derivation=derivation(disclosure=K5),
        )
        if accepted:
            assert ResultEnvelope.model_validate(value)
        else:
            assert "disclosure" in error_types(ResultEnvelope, value)


def test_a_small_lift_differs_count_is_not_shown() -> None:
    value = envelope(
        population=[known_population(lift_differs=2), known_population()],
        derivation=derivation(disclosure=K5),
        caveats=[caveat("LIFT_DIFFERS", ["/population/0/lift_differs"])],
    )
    assert "disclosure" in error_types(ResultEnvelope, value)
    value["population"][0]["lift_differs"] = 5
    assert ResultEnvelope.model_validate(value)


def test_a_breakdown_with_a_small_count_is_refused_whatever_its_total() -> None:
    small = analysed(n=10, excluded=excluded(NOT_APPLICABLE=5, INVALID_VALUE=2), excluded_units=5)
    value = _under_k(
        population=[known_population(n_true=15), known_population()], analysed=[small, analysed()]
    )
    value["caveats"].append(caveat("INVALID_EXCLUDED", ["/analysed/0"], "warn"))
    assert "disclosure" in error_types(ResultEnvelope, value)
    value["analysed"][0]["excluded"] = excluded(NOT_APPLICABLE=5, INVALID_VALUE=5)
    assert ResultEnvelope.model_validate(value)


def test_zero_analysed_counts_would_show_a_suppressed_n_true() -> None:
    hidden = known_population(n_true=None, n_false=None, suppressed=["/n_false", "/n_true"])
    value = _under_k(
        population=[hidden, known_population()],
        analysed=[analysed(n=0, excluded_units=0), analysed()],
    )
    value["caveats"].append(caveat("SUPPRESSED", ["/population/0"]))
    assert "disclosure" in error_types(ResultEnvelope, value)


def test_a_cohort_counts_size_has_no_excluded_member_even_null() -> None:
    size = {
        **cohort_count()["size"],
        "excluded": None,
        "not_estimable": {"/excluded": "suppressed"},
    }
    count = cohort_count(size=size, disclosure=K5, caveats=[caveat("SUPPRESSED", ["/population"])])
    assert "count_size" in error_types(CohortCount, count)


def test_caveats_may_affect_a_cohorts_entry_and_nothing_else_by_name() -> None:
    assert ResultEnvelope.model_validate(
        envelope(caveats=[caveat("SMALL_N", ["/cohorts/0"], "warn")])
    )
    for affects in (["/valuesx"], ["/cohortsx/0"], ["/readback"]):
        value = envelope(caveats=[caveat("SMALL_N", affects, "warn")])
        assert error_types(ResultEnvelope, value)
        dumped = ResultEnvelope.model_validate(envelope()).model_dump(mode="json")
        dumped["caveats"] = [caveat("SMALL_N", affects, "warn")]
        assert _schema_refuses("result.schema.json", dumped)


def _size_schema_refuses(**changes: Any) -> bool:
    dumped = CohortCount.model_validate(cohort_count()).model_dump(mode="json")
    dumped["size"] = {**dumped["size"], **changes}
    return _schema_refuses("cohort-count.schema.json", dumped)


def test_the_count_schema_states_the_rules_of_proportions() -> None:
    interval = {"method": "wilson", "level": 0.95, "low": 0.4, "high": 0.9}
    assert not _size_schema_refuses(ci=interval)
    assert _size_schema_refuses(ci={**interval, "low": None})  # no reason for the null
    assert _size_schema_refuses(ci={**interval, "low": -1e300})  # beyond ±(2^53 - 1)
    suppressed = {"/ci/low": "suppressed", "/ci/high": "suppressed"}
    hidden = {**interval, "low": None, "high": None}
    assert _size_schema_refuses(ci=hidden, not_estimable=suppressed)  # both counts shown


def test_suppressed_counts_are_named_by_their_members_in_the_schema() -> None:
    dumped = ResultEnvelope.model_validate(envelope()).model_dump(mode="json")
    dumped["analysed"][0]["not_estimable"] = {"/n": "suppressed"}  # n is shown
    assert _schema_refuses("result.schema.json", dumped)
    count = CohortCount.model_validate(cohort_count()).model_dump(mode="json")
    count["population"]["suppressed"] = ["/foo"]
    assert _schema_refuses("cohort-count.schema.json", count)


@pytest.mark.parametrize("name", sorted(OUTPUT_SCHEMAS))
def test_no_output_schema_defaults_a_member_to_null(name: str) -> None:
    """An absent member is never null, which would mean suppressed or not estimable."""
    text = json.dumps(SCHEMAS[name]())
    assert '"default": null' not in text


def test_an_absent_limit_is_not_written_as_null() -> None:
    from aibi.core.schema.refusals import Refusal, RefusalCode

    refusal = Refusal(code=RefusalCode.WRONG_TYPE, path=None, message=[], limit=None)
    assert "limit" not in refusal.model_dump()
    assert "limit" not in json.loads(refusal.model_dump_json())
    assert refusal.model_dump()["path"] is None  # a required member keeps its null


def test_a_nested_output_built_without_validation_is_validated_where_it_is_used() -> None:
    result = ResultEnvelope.model_validate(envelope())
    issuance = Issuance.model_construct(id="iss:not-an-id", cache_hit=False, values_from=ISSUANCE)
    with pytest.raises(ValidationError):
        ResultEnvelope.model_validate({**dict(result), "issuance": issuance})


def test_a_value_that_holds_itself_is_refused_without_hanging() -> None:
    result = ResultEnvelope.model_validate(envelope())
    loop: list[Any] = []
    loop.append(loop)
    values = Values.model_construct(positions=[{"x": loop}, {}], view={})
    smuggled = ResultEnvelope.model_construct(**{**dict(result), "values": values})
    with _deadline(5), pytest.raises((PydanticSerializationError, ValueError)):
        smuggled.model_dump_json()


def test_variables_are_for_views_over_several() -> None:
    from aibi.core.schema.results import Analysed

    one = analysed(variables=[analysed()])
    assert "too_short" in error_types(Analysed, one)
    other = analysed(n=4, excluded=excluded(NO_PARENT=6), excluded_units=6)
    assert Analysed.model_validate(analysed(variables=[analysed(), other]))


# --- Round 4: the unit table accounts for suppressed counts; what dumps; the schema's enums -----


def _accounted(
    parts: tuple[int | None, int | None, int | None], size: int, lift: int | None
) -> Any:
    """A cohort count under k = 5, with ``lift_differs`` given (``None`` suppresses it)."""
    count = _count_with(*parts, size=size or 1)
    unknown = parts[2]
    population = count["population"]
    if unknown:
        by_reason = {reason.value: 0 for reason in Reason} | {"NO_INFORMATION": unknown}
        population.update(unknown_by_reason=by_reason, unknown_by_leaf={LEAF: unknown})
    if not size:
        count["size"].update(denominator=0, estimate=None, not_estimable={"/estimate": "no_units"})
    population["lift_differs"] = lift
    codes = {given["code"] for given in count["caveats"]}
    if lift is None:
        population["suppressed"] = sorted([*population["suppressed"], "/lift_differs"])
        if "SUPPRESSED" not in codes:
            count["caveats"] = [*count["caveats"], caveat("SUPPRESSED", ["/population"])]
    if lift is None or lift:
        lifted = caveat("LIFT_DIFFERS", ["/population"])
        count["caveats"] = [*count["caveats"], lifted]
    return count


@pytest.mark.parametrize(
    ("parts", "size", "lift", "error"),
    [
        ((None, None, 50), 30, 0, "count_size"),  # more unknown units than the table holds
        ((None, None, 30), 30, 0, "count_size"),  # two suppressed counts summing to 0
        ((None, None, 20), 25, 100, "population_lift"),
        ((0, None, 0), 0, 0, "count_size"),  # a suppressed 0
        ((None, 0, 0), 40, 0, "count_size"),  # a suppressed 40, which is not small
        ((10, None, None), 10, 0, "count_size"),  # two suppressed zeros
        ((None, None, 20), 25, None, None),
        ((None, 0, 0), 3, 0, None),
        ((10, None, None), 12, 0, "disclosure"),  # the complement, 2, is small
    ],
)
def test_the_unit_table_accounts_for_suppressed_counts(
    parts: tuple[int | None, int | None, int | None], size: int, lift: int | None, error: str | None
) -> None:
    """Each suppressed count is from 1 to k - 1, so it counts at least one unit, and one
    suppressed beside zeros is the whole table (§8.4)."""
    count = _accounted(parts, size, lift)
    if error is None:
        assert CohortCount.model_validate(count)
    else:
        assert error in error_types(CohortCount, count)


def test_with_n_true_shown_n_and_excluded_units_are_shown_or_suppressed_together() -> None:
    """n + excluded_units is n_true: one shown alone would show the other, even a 0."""
    shown_n = analysed(n=0, excluded_units=None, excluded=None)
    shown_n["not_estimable"] = {"/excluded_units": "suppressed", "/excluded": "suppressed"}
    shown_excluded = analysed(n=None, excluded_units=0, not_estimable={"/n": "suppressed"})
    for alone in (shown_n, shown_excluded):
        value = envelope(
            population=[known_population(n_true=0, n_false=15), known_population()],
            analysed=[alone, analysed()],
            derivation=derivation(disclosure=K5),
            caveats=[caveat("SUPPRESSED", ["/analysed/0"])],
        )
        assert "disclosure" in error_types(ResultEnvelope, value)


def test_n_is_at_most_the_sum_of_the_variables_n() -> None:
    from aibi.core.schema.results import Analysed

    part = analysed(n=4, excluded=excluded(NO_PARENT=6), excluded_units=6)
    assert "analysed_variables" in error_types(Analysed, analysed(n=10, variables=[part, part]))
    within = analysed(n=8, excluded=excluded(NO_PARENT=2), excluded_units=2, variables=[part, part])
    assert Analysed.model_validate(within)


class _Computed(BaseModel):
    @computed_field  # type: ignore[prop-decorator]
    @property
    def x(self) -> float:
        return math.nan


class _Hiding(dict[str, Any]):
    def values(self) -> Any:
        return []

    def __iter__(self) -> Any:
        return iter(())


class _Listing(list[Any]):
    pass


class _Small(int):
    def __abs__(self) -> int:
        return 0


def _smuggled(value: object) -> Values:
    return Values.model_construct(positions=[{"x": value}, {}], view={})


_SMUGGLED: dict[str, object] = {
    "another model": _Computed(),
    "dict subclass": _Hiding(x=math.nan),
    "list subclass": _Listing([math.nan]),
    "int subclass": _Small(2**60),
    "big float": 2.0**60,
    "surrogate key": {chr(0xD800): 1},
}


@pytest.mark.parametrize("value", _SMUGGLED.values(), ids=_SMUGGLED.keys())
@pytest.mark.parametrize("mode", ["python", "json", "text"])
def test_only_outputs_and_json_values_of_python_s_own_types_dump(value: object, mode: str) -> None:
    """model_construct can put anything in; a subclass or another model could dump otherwise
    than its members say, so it is refused in every mode (§8.2)."""
    warnings.simplefilter("ignore")  # Pydantic warns of what it does not expect, and goes on
    smuggled = _smuggled(value)
    dump: Callable[[], object] = (
        smuggled.model_dump_json if mode == "text" else lambda: smuggled.model_dump(mode=mode)
    )
    with pytest.raises((PydanticSerializationError, ValueError)):
        dump()


def test_a_value_that_holds_itself_is_refused_in_every_mode() -> None:
    warnings.simplefilter("ignore")
    loop: list[Any] = []
    loop.append(loop)
    for dump in (_smuggled(loop).model_dump, _smuggled(loop).model_dump_json):
        with _deadline(5), pytest.raises((PydanticSerializationError, ValueError)):
            dump()


def test_values_held_in_many_places_and_tuples_dump() -> None:
    warnings.simplefilter("ignore")  # Pydantic warns of a tuple where the schema has a list
    shared = {"a": 1.5}
    positions = [{"x": [shared] * 3, "t": (1, 2)}, {"y": shared}]
    values = Values.model_construct(positions=positions, view={})
    assert json.loads(values.model_dump_json())["positions"][0]["t"] == [1, 2]
    assert values.model_dump()["positions"][1] == {"y": {"a": 1.5}}


def test_the_schemas_accept_every_suppressed_count_and_every_pointer_not_estimable() -> None:
    count = _accounted((None, None, 20), 25, None)
    dumped = CohortCount.model_validate(count).model_dump(mode="json")
    assert not _schema_refuses("cohort-count.schema.json", dumped)
    interval = {"method": "wilson", "level": 0.95, "low": None, "high": None}
    reasons = {"/estimate": "no_units", "/ci/low": "no_units", "/ci/high": "no_units"}
    zero = {"estimate": None, "numerator": 0, "denominator": 0, "ci": interval}
    dumped = CohortCount.model_validate(cohort_count()).model_dump(mode="json")
    dumped["size"] = {**dumped["size"], **zero, "not_estimable": reasons}
    dumped["population"] = {**dumped["population"], "n_true": 0, "n_false": 0, "n_unknown": 0}
    assert not _schema_refuses("cohort-count.schema.json", dumped)


def test_the_count_schema_says_a_count_over_a_draft_is_never_cached() -> None:
    dumped = CohortCount.model_validate(cohort_count()).model_dump(mode="json")
    dumped["releases"] = [{**release, "status": "draft"} for release in dumped["releases"]]
    dumped["caveats"] = [caveat("DRAFT_RELEASE", ["/population"], "warn")]
    dumped["issuance"] = {**dumped["issuance"], "cache_hit": True}
    assert _schema_refuses("cohort-count.schema.json", dumped)


# --- Round 5: what an enumeration member dumps as; the pass's image; the walk's cost ------------


class _Plain(Enum):
    A = "a"


class _Pretender(IntEnum):
    """Its value is 1; the int it is, and dumps as, is 2^60."""

    def __new__(cls, value: int) -> "_Pretender":
        member = int.__new__(cls, 2**60)
        member._value_ = value
        return member

    A = 1


class _NotANumber(float, Enum):
    """Its value is 1.0; the float it is, and dumps as, is NaN."""

    def __new__(cls, value: float) -> "_NotANumber":
        member = float.__new__(cls, math.nan)
        member._value_ = value
        return member

    A = 1.0


class _Other(IntEnum):
    """Its value is 1; the int it is, and dumps as, is 2."""

    def __new__(cls, value: int) -> "_Other":
        member = int.__new__(cls, 2)
        member._value_ = value
        return member

    A = 1


class _Nan(float, Enum):
    A = math.nan


class _Odd(StrEnum):
    A = chr(0xFFFE)


class _Level(IntEnum):
    A = 1


class _Half(float, Enum):
    A = 0.5


class _Truth(int, Enum):
    """Its value is True; the int it is, and dumps as in text, is 1."""

    def __new__(cls, value: bool) -> "_Truth":
        member = int.__new__(cls, int(value))
        member._value_ = value
        return member

    A = True


class _Rehashed(StrEnum):
    """Written as ``a``, but hashed apart from the text ``a``."""

    A = "a"

    def __hash__(self) -> int:
        return hash(self._name_)


class _Ascii(str):
    def isascii(self) -> bool:
        return True


class _Tiny(float):
    def __abs__(self) -> float:
        return 0.0


class _Hidden(list[Any]):
    def __iter__(self) -> Any:
        return iter(())


_SMUGGLED_TOO: dict[str, object] = {
    "plain enum": _Plain.A,
    "plain enum key": {"a": 1, _Plain.A: 2},
    "int enum that is a big int": _Pretender.A,
    "int enum that is another int": _Other.A,
    "float enum that is NaN": _NotANumber.A,
    "float enum valued NaN": _Nan.A,
    "str enum key of a noncharacter": {_Odd.A: 1},
    "int enum key": {_Level.A: 1},
    "int enum valued True": _Truth.A,
    "str enum key written as another key": {"a": 1, _Rehashed.A: 2},
    "str subclass passing as ASCII": _Ascii(chr(0xFFFE)),
    "str subclass key": {_Ascii(chr(0xD800)): 1},
    "float subclass passing as small": _Tiny(2.0**60),
    "list subclass hiding its items": _Hidden([math.nan]),
}


@pytest.mark.parametrize("value", _SMUGGLED_TOO.values(), ids=_SMUGGLED_TOO.keys())
@pytest.mark.parametrize("mode", ["python", "json", "text"])
def test_an_enumeration_member_or_subclass_dumps_only_as_what_it_holds(
    value: object, mode: str
) -> None:
    """A member dumps as the data of its str, int or float mixin, which must be its value and a
    JSON value; a plain Enum's member is refused, and so is a subclass (§8.2)."""
    warnings.simplefilter("ignore")
    smuggled = _smuggled(value)
    dump: Callable[[], object] = (
        smuggled.model_dump_json if mode == "text" else lambda: smuggled.model_dump(mode=mode)
    )
    with pytest.raises((PydanticSerializationError, ValueError)):
        dump()


def test_str_enumeration_members_dump_as_their_values() -> None:
    warnings.simplefilter("ignore")
    values = _smuggled({"reason": Reason.NOT_COVERED, Reason.NO_ROWS: 1})
    written = json.loads(values.model_dump_json())["positions"][0]
    assert written == {"x": {"reason": "NOT_COVERED", "NO_ROWS": 1}}


def test_int_and_float_enumeration_members_dump_as_their_values() -> None:
    warnings.simplefilter("ignore")
    values = _smuggled({"level": _Level.A, "half": _Half.A})
    assert json.loads(values.model_dump_json())["positions"][0] == {"x": {"level": 1, "half": 0.5}}
    assert values.model_dump(mode="json")["positions"][0] == {"x": {"level": 1, "half": 0.5}}


def test_a_scalar_is_checked_by_itself() -> None:
    from aibi.core.schema.output import OutputError, check_values

    with pytest.raises(OutputError):
        check_values(math.nan)
    check_values(1.5)


@contextlib.contextmanager
def _deadline(seconds: float) -> Iterator[None]:
    """Fail what runs longer than ``seconds``, rather than hang the suite; the expiry is
    remembered, so a caller that swallows the error it raises still fails."""
    expiries: list[float] = []

    def expired(signum: int, frame: object) -> None:
        expiries.append(seconds)
        raise TimeoutError(f"longer than {seconds} s")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert not expiries, f"longer than {seconds} s"


def test_a_value_that_holds_itself_is_refused_within_a_deadline() -> None:
    warnings.simplefilter("ignore")
    loop: list[Any] = []
    loop.append({"again": loop})
    with _deadline(5), pytest.raises((PydanticSerializationError, ValueError)):
        _smuggled(loop).model_dump_json()


def test_a_container_held_in_many_places_is_walked_once() -> None:
    """A value held twice at each of 40 levels is 2^40 paths, but 40 containers; a dict with
    many keys held many times is scanned once."""
    from aibi.core.schema.output import check_values

    shared: Any = [1.5]
    for _ in range(40):
        shared = [shared, shared]
    wide = {f"k{index}": index for index in range(10_000)}
    with _deadline(5):
        check_values(shared)
        check_values([wide] * 20_000)


def _population_of(k: int, parts: tuple[int | None, int | None, int | None], size: int) -> Any:
    """A cohort count under ``k`` with these counts, ``None`` suppressed."""
    count = _accounted(parts, size, 0)
    count["disclosure"] = {"min_cell_count": k}
    return count


@pytest.mark.parametrize(
    ("k", "parts", "size", "accepted"),
    [
        # Two suppressed beside a shown 10: both small (4 + 4), or one small and the smallest
        # non-zero other, at most 10, or 9 when the shown one is n_true, first in order.
        (5, (None, None, 10), 24, True),  # 4 + 10, n_true 10 first on the tie
        (5, (None, None, 10), 25, False),
        (5, (10, None, None), 23, True),  # 4 + 9
        (5, (10, None, None), 24, False),  # 4 + 10: the tie would suppress n_true
        (5, (None, 10, None), 24, True),
        (5, (None, 0, None), 105, True),  # beside a 0 the second can be any size
        (5, (None, None, 5), 105, False),  # the pass suppresses n_unknown, not 96 or more
        # All three suppressed are small, or n_true is the complement of two small ones.
        (2, (None, None, None), 3, True),
        (2, (None, None, None), 4, False),
        (2, (None, None, None), 10, False),
        (5, (None, None, None), 103, True),  # 100, 2 and 1: the complement 3 is small
        # One suppressed beside zeros is the whole table, which is small.
        (5, (None, 0, 0), 4, True),
        (5, (None, 0, 0), 5, False),
    ],
)
def test_a_cohort_count_shows_only_what_the_pass_can_leave(
    k: int, parts: tuple[int | None, int | None, int | None], size: int, accepted: bool
) -> None:
    count = _population_of(k, parts, size)
    if accepted:
        assert CohortCount.model_validate(count)
    else:
        assert "count_size" in error_types(CohortCount, count)


@pytest.mark.parametrize(("lift", "size", "error"), [(12, 12, None), (None, 0, "population_lift")])
def test_lift_differs_counts_at_most_the_unit_table(
    lift: int | None, size: int, error: str | None
) -> None:
    count = _accounted((None, None, 0) if size else (0, 0, 0), size, lift)
    if error is None:
        assert CohortCount.model_validate(count)
    else:
        assert error in error_types(CohortCount, count)


def test_a_variable_excludes_at_least_the_units_the_whole_excludes() -> None:
    from aibi.core.schema.results import Analysed

    part = analysed(n=None, excluded=excluded(NO_PARENT=6), excluded_units=6)
    part["not_estimable"] = {"/n": "suppressed"}
    whole = analysed(n=None, excluded=excluded(NO_PARENT=7), excluded_units=7)
    whole["not_estimable"] = {"/n": "suppressed"}
    assert "analysed_variables" in error_types(Analysed, {**whole, "variables": [part, part]})
    part = {**part, "excluded": excluded(NO_PARENT=8), "excluded_units": 8}
    assert Analysed.model_validate({**whole, "variables": [part, part]})
    # With a variable's n suppressed, n is not bounded by the sum of theirs.
    shown = analysed(n=6, excluded=excluded(NO_PARENT=6), excluded_units=6)
    hidden = analysed(n=None, excluded=excluded(NO_PARENT=6), excluded_units=6)
    hidden["not_estimable"] = {"/n": "suppressed"}
    total = analysed(n=12, excluded=excluded(), excluded_units=0)
    assert Analysed.model_validate({**total, "variables": [shown, hidden]})


def test_with_n_true_suppressed_n_may_be_suppressed_beside_a_shown_excluded_units() -> None:
    count_n = analysed(n=None, excluded=excluded(NOT_COVERED=5), excluded_units=5)
    count_n["not_estimable"] = {"/n": "suppressed"}
    hidden = known_population(n_true=None, n_false=None, suppressed=["/n_false", "/n_true"])
    value = envelope(
        population=[hidden, known_population()],
        analysed=[count_n, analysed()],
        derivation=derivation(disclosure=K5),
        caveats=[
            caveat("SUPPRESSED", ["/analysed/0", "/population/0"]),
            {**UNKNOWN_EXCLUDED, "affects": ["/analysed/0"]},
        ],
    )
    assert ResultEnvelope.model_validate(value)

"""Hashes, rounding and digests (§7.6, §9.3; D282, D283, D288)."""

import hashlib
from collections.abc import Callable
from typing import Any

import pytest

from aibi.core.engine.canonical import Canonicalisation
from aibi.core.engine.counts import count_parts
from aibi.core.engine.data import Release
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.ids import (
    digest,
    digested,
    hashed,
    leaf_key,
    reduced,
    rounded,
    sorted_unique,
)
from aibi.core.schema.caveats import CORE_SEVERITIES, Caveat, CaveatCode
from aibi.core.schema.output import text

City = Callable[..., Release]
Doc = Callable[..., dict[str, Any]]
Canon = Callable[..., Canonicalisation]


def test_a_hash_is_the_sha256_of_the_rfc_8785_text() -> None:
    assert (
        hashed({"b": 1, "a": [2.0, "é"]})
        == hashlib.sha256(b'{"a":[2,"\xc3\xa9"],"b":1}').hexdigest()
    )
    assert leaf_key({"all": []}) == "leaf:" + hashlib.sha256(b'{"all":[]}').hexdigest()


def test_sorting_compares_serialisations_as_utf16_code_units_and_keeps_one_of_each() -> None:
    # Serialised, "a" starts with a quote, which sorts before digits, and "10" before "9".
    found = sorted_unique(["b", "a", 10, 9, "a", {"x": 1}, [1], 9.0])
    assert found == ["a", "b", 10, 9, [1], {"x": 1}]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (0.1 + 0.2, 0.3),
        (1 / 3, 0.3333333333),
        (2 / 3, 0.6666666667),
        (123456789012.345, 123456789000.0),
        (9e-13, 0.0),
        (-9e-13, 0.0),
        (1e-12, 1e-12),
        (-2.5e-7, -2.5e-7),
        (1.00000000005, 1.0),  # the double just below the tie: rounds down
        (0.0, 0.0),
    ],
)
def test_floats_are_rounded_to_ten_significant_digits_before_hashing(
    given: float, expected: float
) -> None:
    assert rounded(given) == expected


def test_integers_are_never_rounded_and_text_members_are_left_out() -> None:
    value = {
        "count": 12345678901234,
        "estimate": 1 / 3,
        "denominator_text": [{"text": "units"}],
        "nested": [{"label_text": "x", "n": 3, "flag": True}],
    }
    assert digested(value) == {
        "count": 12345678901234,
        "estimate": 0.3333333333,
        "nested": [{"n": 3, "flag": True}],
    }


def test_a_non_finite_number_never_enters_a_digest() -> None:
    with pytest.raises(ValueError, match="finite"):
        rounded(float("inf"))


def _caveat(code: CaveatCode, *affects: str, message: str = "m") -> Caveat:
    return Caveat(
        code=code, severity=CORE_SEVERITIES[code], message=[text(message)], affects=list(affects)
    )


def test_caveats_enter_a_digest_as_code_severity_and_affects_without_draft_release() -> None:
    caveats = [
        _caveat(CaveatCode.UNKNOWN_EXCLUDED, "/population", message="one wording"),
        _caveat(CaveatCode.DRAFT_RELEASE, "/population"),
        _caveat(CaveatCode.LIFT_DIFFERS, "/population"),
        _caveat(CaveatCode.UNKNOWN_EXCLUDED, "/population", message="another wording"),
    ]
    assert reduced(caveats) == [
        {"code": "LIFT_DIFFERS", "severity": "info", "affects": ["/population"]},
        {"code": "UNKNOWN_EXCLUDED", "severity": "warn", "affects": ["/population"]},
    ]
    one = digest({"population": {"n": 1}, "caveats": reduced(caveats)})
    other = digest({"caveats": reduced(caveats[::-1][:3]), "population": {"n": 1.0}})
    assert one == other


def test_a_count_over_a_draft_has_the_digest_of_the_count_over_its_release(
    canon: Canon, city: City, doc: Doc
) -> None:
    release = city({"establishments": [{"establishment_id": "e1"}]})
    written = doc([{"kind": "value", "column": "establishments.seats", "range": {"gt": 3}}])
    counts = []
    for label in (1, "draft"):
        [cohort] = canon(written, release, labels={release.manifest: label}).cohorts.values()
        counts.append(count_parts(cohort, evaluate(cohort.resolved)))
    draft = counts[1]
    assert {c.code for c in draft.caveats} == {"UNKNOWN_EXCLUDED", "DRAFT_RELEASE"}
    assert draft.digest == counts[0].digest


def test_a_count_s_size_is_its_units_over_the_unit_table(
    canon: Canon, city: City, doc: Doc
) -> None:
    rows = {"establishments": [{"establishment_id": f"e{n}", "seats": n} for n in range(3)]}
    written = doc([{"kind": "value", "column": "establishments.seats", "range": {"gte": 1}}])
    [cohort] = canon(written, city(rows)).cohorts.values()
    size = count_parts(cohort, evaluate(cohort.resolved)).size
    assert (size.numerator, size.denominator, size.estimate) == (2, 3, 2 / 3)
    [empty] = canon(written, city()).cohorts.values()
    nothing = count_parts(empty, evaluate(empty.resolved)).size
    assert (nothing.estimate, nothing.not_estimable) == (None, {"/estimate": "no_units"})


def test_a_count_keys_each_clause_s_unknown_units_by_that_clause_s_leaf_key(
    canon: Canon, city: City, doc: Doc
) -> None:
    rows = {
        "establishments": [
            {"establishment_id": "e1"},
            {"establishment_id": "e2", "seats": 3},
            {"establishment_id": "e3", "seats": 5, "frontage": 2.0},
        ]
    }
    seats = {"kind": "value", "column": "establishments.seats", "range": {"gt": 1}}
    frontage = {"kind": "value", "column": "establishments.frontage", "range": {"gt": 1}}
    [cohort] = canon(doc([seats, frontage]), city(rows)).cohorts.values()
    keys = {
        clause["column"]: key
        for clause, key in zip(cohort.clauses, cohort.keys, strict=True)
        if isinstance(clause, dict)
    }
    population = count_parts(cohort, evaluate(cohort.resolved)).population
    assert population.unknown_by_leaf == {
        keys["establishments.seats"]: 1,
        keys["establishments.frontage"]: 2,
    }


def test_a_count_raises_the_caveats_its_evaluation_and_fields_call_for(
    canon: Canon, city: City, doc: Doc
) -> None:
    rows = {
        "establishments": [{"establishment_id": "e1"}, {"establishment_id": "e2"}],
        "inspections": [{"inspection_id": "i1", "establishment_id": "e1", "kind": "routine"}],
        "inspection_checklists": [{"inspection_id": "i1", "checklist": "basic"}],
        "checklist_items": [{"checklist": "basic", "code": "temp", "all_codes": False}],
    }
    release = city(rows, inspections={"parents": "all", "statuses": {"parents": "proposed"}})
    written = doc(
        [
            {
                "kind": "exists",
                "table": "inspections",
                "lift": "assessed",
                "where": [{"kind": "exists", "table": "violations"}],
            },
            {"kind": "value", "column": "establishments.revenue", "range": {"gt": 1}},
        ]
    )
    [cohort] = canon(written, release).cohorts.values()
    parts = count_parts(cohort, evaluate(cohort.resolved))
    codes = {caveat.code: caveat for caveat in parts.caveats}
    assert set(codes) >= {"UNKNOWN_EXCLUDED", "UNCONFIRMED_SEMANTICS", "COVERAGE_PROPOSED"}
    assert all(caveat.affects == ["/population"] for caveat in parts.caveats)
    fields = [s.model_dump() for s in codes["UNCONFIRMED_SEMANTICS"].message]
    assert {"data": "establishments.revenue/fields/units"} in fields
    relationships = [s.model_dump() for s in codes["COVERAGE_PROPOSED"].message]
    assert {"data": "rel:inspections.establishment"} in relationships


def test_a_float_that_rounds_past_the_largest_double_is_refused() -> None:
    assert rounded(1.797693134e308) == 1.797693134e308
    with pytest.raises(ValueError, match="finite"):
        rounded(1.7976931348623157e308)
    with pytest.raises(ValueError, match="finite"):
        rounded(-1.7976931348623157e308)


def test_a_count_raises_lift_differs_when_the_other_lift_would_change_units(
    canon: Canon, city: City, doc: Doc
) -> None:
    rows = {
        "establishments": [{"establishment_id": "e0"}, {"establishment_id": "e1"}],
        "inspections": [
            {"inspection_id": "i0", "establishment_id": "e0", "kind": "routine"},
            {"inspection_id": "i1", "establishment_id": "e0", "kind": "routine"},
            {"inspection_id": "i2", "establishment_id": "e1", "kind": "routine"},
        ],
        "inspection_checklists": [
            {"inspection_id": "i0", "checklist": "basic"},
            {"inspection_id": "i2", "checklist": "basic"},
        ],
        "checklist_items": [{"checklist": "basic", "code": "pest", "all_codes": False}],
    }
    pest = {"kind": "value", "column": "violations.code", "values": ["pest"]}
    [cohort] = canon(doc([pest]), city(rows)).cohorts.values()
    result = evaluate(cohort.resolved)
    assert result.lift_differs == 1
    [raised] = [c for c in count_parts(cohort, result).caveats if c.code == "LIFT_DIFFERS"]
    assert raised.affects == ["/population"]
    assert raised.message[0] == text("The other lift rule would change the answer for 1 ")
    [none] = canon(doc([]), city(rows)).cohorts.values()
    assert "LIFT_DIFFERS" not in {
        c.code for c in count_parts(none, evaluate(none.resolved)).caveats
    }

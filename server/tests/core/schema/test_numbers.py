"""Numbers, proportions and effect sizes (SPEC §8.2)."""

import math
from typing import Any

import jsonschema
import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_core import PydanticSerializationError

from aibi.core.schema.numbers import (
    EffectSize,
    Interval,
    Proportion,
    not_estimable_problems,
)

KNOWN = {"position": 0, "predicate": "leaf:" + "a" * 64, "counts": "known"}


def proportion(**members: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "estimate": 0.25,
        "numerator": 1,
        "denominator": 4,
        "denominator_definition": KNOWN,
    }
    return {**base, **members}


def error_types(model: type[Any], value: dict[str, Any]) -> list[str]:
    with pytest.raises(ValidationError) as raised:
        model.model_validate(value)
    return [str(error["type"]) for error in raised.value.errors()]


def test_a_proportion_names_its_counts_and_denominator() -> None:
    parsed = Proportion.model_validate(proportion())
    assert parsed.model_dump() == proportion()
    for member in ("estimate", "numerator", "denominator", "denominator_definition"):
        assert "missing" in error_types(
            Proportion, {k: v for k, v in proportion().items() if k != member}
        )


@pytest.mark.parametrize(
    ("members", "error"),
    [
        ({"numerator": 5}, "proportion_bounds"),
        ({"estimate": 0.3}, "proportion_estimate"),
        ({"estimate": -0.1, "numerator": 0}, "proportion_estimate"),
        ({"estimate": None}, "not_estimable_missing"),
        ({"not_estimable": {"/estimate": "zero_denominator"}}, "not_estimable_extra"),
        ({"not_estimable": {}}, "not_estimable_empty"),
        (
            {
                "numerator": None,
                "not_estimable": {"/numerator": "suppressed"},
            },
            "proportion_suppressed",
        ),
        (
            {"estimate": None, "numerator": 0, "denominator": 0},
            "not_estimable_missing",
        ),
        (
            {
                "estimate": None,
                "numerator": 0,
                "denominator": 0,
                "not_estimable": {"/estimate": "not_reached"},
            },
            "proportion_zero_denominator",
        ),
    ],
)
def test_proportion_rules(members: dict[str, Any], error: str) -> None:
    assert error in error_types(Proportion, proportion(**members))


@pytest.mark.parametrize(
    ("counts", "allowed"),
    [
        ("known", {"no_units"}),
        ("unit_table", {"no_units"}),
        ("rows", {"no_units", "zero_denominator"}),
    ],
)
def test_a_zero_denominator_is_not_estimable_in_the_order_of_the_reasons(
    counts: str, allowed: set[str]
) -> None:
    """Counting units, a zero denominator means no unit was counted (SPEC §8.2, §9.5)."""
    definition = {**KNOWN, "counts": counts}
    if counts == "unit_table":
        definition = {"position": None, "predicate": None, "counts": counts}
    for reason in ("no_units", "zero_denominator", "no_events"):
        value = proportion(
            estimate=None,
            numerator=0,
            denominator=0,
            denominator_definition=definition,
            not_estimable={"/estimate": reason},
        )
        if reason in allowed:
            assert Proportion.model_validate(value).reasons() == {"/estimate": reason}
        else:
            assert "proportion_zero_denominator" in error_types(Proportion, value)


def test_the_estimate_is_the_double_numerator_over_denominator() -> None:
    assert Proportion.model_validate(proportion(estimate=124 / 301, numerator=124, denominator=301))
    rounded = proportion(estimate=0.4119601329, numerator=124, denominator=301)
    assert "proportion_estimate" in error_types(Proportion, rounded)


def test_an_interval_is_suppressed_only_with_a_count() -> None:
    value = proportion(
        ci={"method": "wilson", "level": 0.95, "low": None, "high": None},
        not_estimable={"/ci/low": "suppressed", "/ci/high": "suppressed"},
    )
    assert "proportion_suppressed" in error_types(Proportion, value)
    value["not_estimable"] = {"/ci/low": "not_converged", "/ci/high": "not_converged"}
    assert Proportion.model_validate(value)


def test_an_interval_method_is_an_identifier() -> None:
    value = proportion(ci={"method": "a__b", "level": 0.95, "low": 0.1, "high": 0.4})
    assert error_types(Proportion, value)


def test_suppressed_counts_suppress_the_estimate_and_its_interval() -> None:
    value = proportion(
        estimate=None,
        numerator=None,
        ci={"method": "wilson", "level": 0.95, "low": None, "high": None},
        not_estimable={
            "/estimate": "suppressed",
            "/numerator": "suppressed",
            "/ci/low": "suppressed",
            "/ci/high": "suppressed",
        },
    )
    assert Proportion.model_validate(value)
    with_bounds = {**value, "ci": {"method": "wilson", "level": 0.95, "low": 0.1, "high": 0.4}}
    with_bounds["not_estimable"] = {"/estimate": "suppressed", "/numerator": "suppressed"}
    assert "proportion_interval" in error_types(Proportion, with_bounds)


def test_nested_nulls_are_keyed_from_the_enclosing_object() -> None:
    value = proportion(
        ci={"method": "wilson", "level": 0.95, "low": 0.05, "high": None},
        not_estimable={"/ci/high": "not_reached"},
    )
    assert Proportion.model_validate(value).ci is not None
    assert "not_estimable_missing" in error_types(Proportion, {**value, "not_estimable": None})


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_numbers_are_refused(bad: float) -> None:
    with pytest.raises(ValidationError):
        Proportion.model_validate(proportion(estimate=bad))
    with pytest.raises(ValidationError):
        Interval.model_validate({"method": "wald", "level": 0.95, "low": bad, "high": 1.0})


def test_non_finite_numbers_cannot_be_serialised() -> None:
    smuggled = Interval.model_construct(method="wald", level=0.95, low=math.nan, high=1.0)
    with pytest.raises(PydanticSerializationError):
        smuggled.model_dump()
    with pytest.raises(PydanticSerializationError):
        smuggled.model_dump_json()
    too_large = Proportion.model_construct(
        estimate=0.5,
        numerator=2**60,
        denominator=2**61,
        denominator_definition=Proportion.model_validate(proportion()).denominator_definition,
    )
    with pytest.raises(PydanticSerializationError):
        too_large.model_dump()


def test_intervals() -> None:
    assert "interval_order" in error_types(
        Interval, {"method": "wald", "level": 0.95, "low": 2.0, "high": 1.0}
    )
    assert "less_than" in error_types(
        Interval, {"method": "wald", "level": 1.0, "low": 0.0, "high": 1.0}
    )


def effect(**members: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"measure": "hazard_ratio", "position": 1, "versus": 0, "estimate": 1.8}
    return {**base, **members}


def test_effect_sizes() -> None:
    assert EffectSize.model_validate(effect()).measure == "hazard_ratio"
    assert "effect_versus" in error_types(EffectSize, effect(versus=1))
    assert "effect_ratio" in error_types(EffectSize, effect(estimate=0.0))
    assert "effect_ratio" in error_types(
        EffectSize, effect(ci={"method": "wald", "level": 0.95, "low": -0.1, "high": 2.0})
    )
    difference = effect(measure="mean_difference", estimate=-3.5)
    assert EffectSize.model_validate(difference).estimate == -3.5
    assert "enum" in error_types(EffectSize, effect(measure="odds_ratio"))


def test_not_estimable_maps_inside_json_values() -> None:
    good: JsonValue = {"median": {"estimate": None, "not_estimable": {"/estimate": "not_reached"}}}
    assert not_estimable_problems(good) == []
    assert not_estimable_problems(
        {"m": {"estimate": 1.0, "not_estimable": {"/estimate": "no_units"}}}
    )
    assert not_estimable_problems({"m": {"x": None, "not_estimable": {"/x": "because"}}})
    assert not_estimable_problems({"m": {"not_estimable": {}}})
    assert not_estimable_problems([{"not_estimable": {"/missing": "no_units"}}])


def test_a_zero_denominator_may_mean_no_units() -> None:
    no_units = proportion(
        estimate=None, numerator=0, denominator=0, not_estimable={"/estimate": "no_units"}
    )
    assert Proportion.model_validate(no_units)


def test_a_suppressed_breakdown_is_null_and_kept() -> None:
    reasons = dict.fromkeys(
        (
            "NOT_ASSESSED",
            "NOT_COVERED",
            "NO_INFORMATION",
            "NO_PARENT",
            "OUT_OF_SCOPE",
            "NO_ROWS",
            "NOT_APPLICABLE",
            "INVALID_VALUE",
        ),
        0,
    )
    shown = Proportion.model_validate(proportion(excluded=reasons))
    assert shown.model_dump()["excluded"] == reasons
    hidden = Proportion.model_validate(
        proportion(excluded=None, not_estimable={"/excluded": "suppressed"})
    )
    assert hidden.model_dump()["excluded"] is None
    assert Proportion.model_validate_json(hidden.model_dump_json()) == hidden
    assert "excluded" not in Proportion.model_validate(proportion()).model_dump()
    assert "proportion_excluded" in error_types(Proportion, proportion(excluded={"NOT_COVERED": 1}))
    wrong = proportion(excluded=None, not_estimable={"/excluded": "no_units"})
    assert "proportion_counts" in error_types(Proportion, wrong)


def test_counts_are_null_only_when_suppressed_and_the_estimate_with_them() -> None:
    wrong = proportion(
        estimate=None,
        numerator=None,
        not_estimable={"/estimate": "no_events", "/numerator": "no_events"},
    )
    assert "proportion_counts" in error_types(Proportion, wrong)
    alone = proportion(estimate=None, not_estimable={"/estimate": "suppressed"})
    assert "proportion_suppressed" in error_types(Proportion, alone)


def test_numbers_beyond_the_safe_range_are_refused() -> None:
    with pytest.raises(ValidationError):
        EffectSize.model_validate(
            {"measure": "mean_difference", "position": 1, "versus": 0, "estimate": 1e20}
        )


def test_not_estimable_maps_in_json_are_non_empty_objects_of_pointers() -> None:
    assert not_estimable_problems({"m": {"x": None, "not_estimable": None}}) == ["/m/not_estimable"]
    assert not_estimable_problems(
        {"m": {"stimate": None, "not_estimable": {"Xstimate": "no_units"}}}
    ) == ["/m/not_estimable/Xstimate"]
    assert not_estimable_problems({"m": {"x": None, "not_estimable": {"/x": "no_units"}}}) == []


# --- Round 3: the level's bounds in the schema, copies that keep what was given ---------------


@pytest.mark.parametrize("level", [0, 0.0, 1, 1.0, 1.5, 5, -1])
def test_a_level_outside_zero_to_one_is_refused_by_the_model_and_the_schema(level: float) -> None:
    value = {"method": "wald", "level": level, "low": 0.0, "high": 1.0}
    with pytest.raises(ValidationError):
        Interval.model_validate(value)
    schema = TypeAdapter(Interval).json_schema()
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, schema)
    jsonschema.validate({**value, "level": 0.95}, schema)


def test_a_copy_with_an_update_keeps_the_members_left_out_absent() -> None:
    """Only the members given are passed on: a default written out as given would change the
    copy (``not_estimable`` would be given, and empty)."""
    given = Proportion.model_validate(proportion())
    copied = given.model_copy(update={"denominator_text": [{"text": "loans"}]})
    assert copied.model_fields_set == given.model_fields_set | {"denominator_text"}
    assert copied.model_dump() == {**given.model_dump(), "denominator_text": [{"text": "loans"}]}
    assert given.model_copy() == given


def test_not_estimable_names_only_members_in_the_schema() -> None:
    schema = TypeAdapter(Proportion).json_schema()
    suppressed = {"/estimate": "suppressed", "/numerator": "suppressed"}
    hidden = proportion(estimate=None, numerator=None, not_estimable=suppressed)
    Proportion.model_validate(hidden)
    jsonschema.validate(hidden, schema)
    unknown = {**hidden, "not_estimable": {**suppressed, "/foo": "suppressed"}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(unknown, schema)
    with pytest.raises(ValidationError):
        Proportion.model_validate(unknown)

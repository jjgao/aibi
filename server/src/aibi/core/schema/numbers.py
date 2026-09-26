"""Numbers, proportions and effect sizes (SPEC §8.2).

Results contain no non-finite numbers. A number that cannot be computed is ``null``, and its
reason is in the enclosing object's ``not_estimable`` map, keyed by a JSON Pointer relative to
that object. Every proportion is an object with its numerator, denominator and denominator
definition, never a bare number.
"""

import re
from collections.abc import Iterator
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, get_args

from pydantic import AllowInfNan, BaseModel, ConfigDict, Field, JsonValue, model_validator

from aibi.core.schema.errors import problem
from aibi.core.schema.ids import MAX_SAFE_INTEGER, Identifier, LeafKey
from aibi.core.schema.jsonio import MISSING, escape_token, lookup
from aibi.core.schema.limits import MAX_COHORTS, MAX_POINTER
from aibi.core.schema.output import COMPUTED, DATA_MARK, LAX, Count, Finite, Output, Segment
from aibi.core.schema.semantics import ExclusionReason

RELATIVE_POINTER = r"^(?:/(?:[^~/]|~[01])*)+$"
_RELATIVE_POINTER_RE = re.compile(RELATIVE_POINTER)
RelativePointer = Annotated[
    str, Field(pattern=RELATIVE_POINTER, max_length=MAX_POINTER, json_schema_extra=DATA_MARK)
]
"""A JSON Pointer into the enclosing object; never the whole object. Its tokens can be keys
from data, so it is data (A6)."""

Position = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER)]
"""A cohort's position in its view."""

Number = Annotated[Finite | None, COMPUTED]
"""A number computed from data, ``null`` when not estimable."""

ComputedCount = Annotated[Count | None, COMPUTED]
"""A count, ``null`` when suppressed (SPEC §8.4)."""


class NotEstimableReason(StrEnum):
    NO_UNITS = "no_units"
    NO_EVENTS = "no_events"
    ZERO_DENOMINATOR = "zero_denominator"
    ZERO_VARIANCE = "zero_variance"
    NOT_REACHED = "not_reached"
    BEYOND_FOLLOW_UP = "beyond_follow_up"
    SEPARATION = "separation"
    NOT_CONVERGED = "not_converged"
    DEGENERATE_TABLE = "degenerate_table"
    OVERLAPPING_COHORTS = "overlapping_cohorts"
    CONFOUNDED_WITH_DATASET = "confounded_with_dataset"
    SUPPRESSED = "suppressed"
    """Computed, then suppressed by the disclosure settings (SPEC §8.4)."""


class EffectMeasure(StrEnum):
    RISK_DIFFERENCE = "risk_difference"
    RISK_RATIO = "risk_ratio"
    PROPORTION_DIFFERENCE = "proportion_difference"
    MEAN_DIFFERENCE = "mean_difference"
    MEDIAN_DIFFERENCE = "median_difference"
    HAZARD_RATIO = "hazard_ratio"


RATIO_MEASURES = frozenset({EffectMeasure.RISK_RATIO, EffectMeasure.HAZARD_RATIO})
"""Ratios are position over reference; the other measures are position minus reference."""


def computed_nulls(model: BaseModel, prefix: str = "") -> Iterator[str]:
    """Pointers to the ``null`` computed members of a model and of the models nested in it.

    Nested estimable models keep their own ``not_estimable`` maps and are not entered.
    """
    for name, info in type(model).model_fields.items():
        if name == "not_estimable" or (
            not info.is_required() and name not in model.model_fields_set
        ):
            continue  # an optional member that is absent is not a null
        value: object = getattr(model, name)
        path = prefix + "/" + escape_token(info.serialization_alias or info.alias or name)
        if value is None:
            if COMPUTED in info.metadata:
                yield path
        elif isinstance(value, Estimable):
            continue
        elif isinstance(value, BaseModel):
            yield from computed_nulls(value, path)
        elif isinstance(value, list):
            for index, item in enumerate(value):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
                if isinstance(item, BaseModel) and not isinstance(item, Estimable):
                    yield from computed_nulls(item, f"{path}/{index}")


_EVERY_EXCLUSION_REASON: dict[str, JsonValue] = {
    "required": [reason.value for reason in ExclusionReason]
}
ExclusionCounts = Annotated[
    dict[Annotated[ExclusionReason, LAX], Count],
    Field(json_schema_extra=_EVERY_EXCLUSION_REASON),
]
"""Counts under every exclusion reason, zeros included (SPEC §6.6, §8.1)."""


def _computed_paths(model: type[BaseModel]) -> list[list[str]]:
    """The member names leading to each computed member of an estimable model, and of a model
    nested in it without a map of its own (an interval's bounds)."""
    paths: list[list[str]] = []
    for name, info in model.model_fields.items():
        key = info.serialization_alias or info.alias or name
        if COMPUTED in info.metadata:
            paths.append([key])
            continue
        nested = [
            member
            for member in (info.annotation, *get_args(info.annotation))
            if isinstance(member, type) and issubclass(member, BaseModel)
        ]
        for member in nested:
            if not issubclass(member, Estimable):
                paths.extend([key, *path] for path in _computed_paths(member))
    return paths


def _null_at(path: list[str]) -> dict[str, Any]:
    """JSON Schema: the member at ``path`` is given as ``null``."""
    condition: dict[str, Any] = {"type": "null"}
    for name in reversed(path):
        condition = {"required": [name], "properties": {name: condition}}
    return condition


def _keyed(pointer: str) -> dict[str, Any]:
    return {"required": ["not_estimable"], "properties": {"not_estimable": {"required": [pointer]}}}


def estimable_schema(schema: dict[str, Any], model: type[BaseModel]) -> None:
    """JSON Schema: a computed member is ``null`` exactly when ``not_estimable`` names it."""
    rules: list[dict[str, Any]] = []
    pointers: list[JsonValue] = []
    for path in _computed_paths(model):
        pointer = "".join("/" + escape_token(name) for name in path)
        pointers.append(pointer)
        rules.append({"if": _null_at(path), "then": _keyed(pointer)})
        rules.append({"if": _keyed(pointer), "then": _null_at(path)})
    if rules:
        rules.append({"properties": {"not_estimable": {"propertyNames": {"enum": pointers}}}})
        schema.setdefault("allOf", []).extend(rules)


class Estimable(Output):
    """An output object whose computed members may be not estimable (SPEC §8.2).

    Every ``null`` computed member, here or in a nested object without a map of its own, has an
    entry in ``not_estimable``, and every entry names such a member. The map is omitted when
    empty.
    """

    model_config = ConfigDict(json_schema_extra=estimable_schema)

    not_estimable: (
        Annotated[
            dict[RelativePointer, Annotated[NotEstimableReason, LAX]],
            Field(json_schema_extra={"minProperties": 1}),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def _check_not_estimable(self) -> Self:
        given = self.not_estimable
        if given is not None and not given:
            raise problem("not_estimable_empty", "An empty not_estimable map is omitted")
        nulls = set(computed_nulls(self))
        keys = set(given or {})
        if missing := sorted(nulls - keys):
            raise problem(
                "not_estimable_missing",
                "Every null number needs its reason in not_estimable: {members}",
                members=", ".join(missing),
            )
        if extra := sorted(keys - nulls):
            raise problem(
                "not_estimable_extra",
                "not_estimable names members that are not null numbers: {members}",
                members=", ".join(extra),
            )
        return self

    def reasons(self) -> dict[str, NotEstimableReason]:
        return dict(self.not_estimable or {})


def not_estimable_problems(value: JsonValue, path: str = "") -> list[str]:
    """The pointers to wrong ``not_estimable`` maps or entries inside a JSON value, such as
    analysis values, sorted.

    A map is a non-empty object. Each key is a pointer relative to the object holding the map,
    naming a ``null`` member of it, and each value is a reason.
    """
    problems: list[str] = []
    pending: list[tuple[JsonValue, str]] = [(value, path)]
    reasons = {reason.value for reason in NotEstimableReason}
    while pending:
        current, where = pending.pop()
        if isinstance(current, list):
            pending.extend((item, f"{where}/{index}") for index, item in enumerate(current))
            continue
        if not isinstance(current, dict):
            continue
        pending.extend(
            (member, f"{where}/{escape_token(key)}")
            for key, member in current.items()
            if key != "not_estimable"
        )
        if "not_estimable" not in current:
            continue
        marks = current["not_estimable"]
        at = f"{where}/not_estimable"
        if not isinstance(marks, dict) or not marks:
            problems.append(at)
            continue
        for key, reason in marks.items():
            named = lookup(current, key) if _RELATIVE_POINTER_RE.fullmatch(key) else MISSING
            if named is not None or not isinstance(reason, str) or reason not in reasons:
                problems.append(f"{at}/{escape_token(key)}")
    return sorted(problems)


Level = Annotated[float, AllowInfNan(False), Field(gt=0, lt=1)]
"""An interval's level: strictly between 0 and 1, and so finite. The bounds sit on the float
itself, before any validator, so that the JSON Schema states them as keywords."""


class Interval(Output):
    """A confidence interval. ``method`` names the method as the analysis entry does (§9.1)."""

    method: Identifier
    level: Level
    low: Number
    high: Number

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if self.low is not None and self.high is not None and self.low > self.high:
            raise problem("interval_order", "An interval's low bound is above its high bound")
        return self


class DenominatorDefinition(Output):
    """What a proportion's denominator counts (SPEC §8.2).

    ``counts`` is ``known`` (the units of a cohort for which a predicate is known), ``unit_table``
    (every unit of the unit table) or ``rows``.
    """

    position: Position | None
    predicate: LeafKey | None
    counts: Literal["known", "unit_table", "rows"]


def _no_default(schema: dict[str, Any]) -> None:
    """JSON Schema: an absent member is not ``null``, which would mean suppressed."""
    schema.pop("default", None)


_SUPPRESSED: dict[str, Any] = {"const": NotEstimableReason.SUPPRESSED.value}
_NOT_SUPPRESSED: dict[str, Any] = {"not": _SUPPRESSED}
_COUNTS_SHOWN: dict[str, Any] = {
    "required": ["numerator", "denominator"],
    "properties": {"numerator": {"type": "integer"}, "denominator": {"type": "integer"}},
}


def _proportion_schema(schema: dict[str, Any], model: type[BaseModel]) -> None:
    """JSON Schema: counts are null only when suppressed, and the estimate and its interval are
    suppressed exactly when a count is (SPEC §8.2, §8.4)."""
    estimable_schema(schema, model)
    counts = dict.fromkeys(("/numerator", "/denominator", "/excluded"), _SUPPRESSED)
    schema["allOf"] += [
        {"properties": {"not_estimable": {"properties": counts}}},
        {
            "if": _COUNTS_SHOWN,
            "then": {
                "properties": {
                    "not_estimable": {
                        "properties": dict.fromkeys(
                            ("/estimate", "/ci/low", "/ci/high"), _NOT_SUPPRESSED
                        )
                    }
                }
            },
            "else": {
                "required": ["not_estimable"],
                "properties": {
                    "not_estimable": {
                        "required": ["/estimate"],
                        "properties": {"/estimate": _SUPPRESSED},
                    }
                },
            },
        },
    ]


class Proportion(Estimable):
    model_config = ConfigDict(json_schema_extra=_proportion_schema)

    estimate: Number
    numerator: ComputedCount
    denominator: ComputedCount
    denominator_definition: DenominatorDefinition
    denominator_text: list[Segment] | None = None
    """Rendered text, outside the digest."""
    excluded: Annotated[ExclusionCounts | None, COMPUTED, Field(json_schema_extra=_no_default)] = (
        None
    )
    """The units or rows left out of the denominator, under every exclusion reason (zeros
    included); ``null`` when suppressed, absent where nothing can be excluded."""
    ci: Interval | None = None

    @model_validator(mode="after")
    def _check_proportion(self) -> Self:
        numerator, denominator, estimate = self.numerator, self.denominator, self.estimate
        reasons = self.reasons()
        suppressed = NotEstimableReason.SUPPRESSED
        if numerator is not None and denominator is not None and numerator > denominator:
            raise problem("proportion_bounds", "A numerator is at most its denominator")
        for member in ("/numerator", "/denominator", "/excluded"):
            if member in reasons and reasons[member] != suppressed:
                raise problem(
                    "proportion_counts", "A proportion's counts are null only when suppressed"
                )
        if self.excluded is not None and set(self.excluded) != set(ExclusionReason):
            raise problem("proportion_excluded", "excluded lists every exclusion reason")
        if numerator is None or denominator is None:
            if reasons.get("/estimate") != suppressed:
                raise problem(
                    "proportion_suppressed",
                    "A proportion is suppressed with its counts (SPEC §8.4)",
                )
        elif reasons.get("/estimate") == suppressed:
            raise problem(
                "proportion_suppressed",
                "A proportion is suppressed only with one of its counts (SPEC §8.4)",
            )
        # Counting units, a zero denominator means no unit was counted; rows may be missing
        # from units that were (§8.2, §9.5).
        counts_units = self.denominator_definition.counts != "rows"
        allowed = (
            {NotEstimableReason.NO_UNITS}
            if counts_units
            else {NotEstimableReason.NO_UNITS, NotEstimableReason.ZERO_DENOMINATOR}
        )
        if denominator == 0 and reasons.get("/estimate") not in allowed:
            raise problem(
                "proportion_zero_denominator",
                "A proportion over a zero denominator is not estimable: no_units when it counts "
                "units, no_units or zero_denominator when it counts rows",
            )
        if (
            estimate is not None
            and numerator is not None
            and denominator
            and estimate != numerator / denominator
        ):
            raise problem(
                "proportion_estimate",
                "A proportion's estimate is numerator / denominator, as a double",
            )
        if numerator is not None and denominator is not None:
            for member in ("/ci/low", "/ci/high"):
                if reasons.get(member) == suppressed:
                    raise problem(
                        "proportion_suppressed",
                        "An interval is suppressed only with one of its counts (SPEC §8.4)",
                    )
        if (
            estimate is None
            and self.ci is not None
            and (self.ci.low is not None or self.ci.high is not None)
        ):
            raise problem(
                "proportion_interval", "A proportion that is not estimable has no interval"
            )
        return self


class EffectSize(Estimable):
    """A contrast of ``position`` against the reference ``versus`` (SPEC §8.2)."""

    measure: Annotated[EffectMeasure, LAX]
    position: Position
    versus: Position
    estimate: Number
    ci: Interval | None = None

    @model_validator(mode="after")
    def _check_effect(self) -> Self:
        if self.position == self.versus:
            raise problem("effect_versus", "An effect size contrasts two different positions")
        if self.measure in RATIO_MEASURES:
            values = [self.estimate]
            if self.ci is not None:
                values += [self.ci.low, self.ci.high]
            if any(value is not None and value <= 0 for value in values):
                raise problem("effect_ratio", "A ratio and its interval bounds are positive")
        return self


Probability = Annotated[float, AllowInfNan(False), Field(ge=0, le=1)]
"""A p-value or a q-value: from 0 to 1."""


class HypothesisTest(Estimable):
    """A test between cohorts (SPEC §8.2, §9.5; D319): ``method`` as the analysis entry names
    it, the positions whose cohorts it used, in view order, and its p-value; its statistic and
    degrees of freedom where the method has them, and its q-value while it is in its view's
    multiple-testing family."""

    method: Identifier
    positions: Annotated[list[Position], Field(max_length=MAX_COHORTS)]
    statistic: Annotated[Finite | None, COMPUTED] = None
    df: Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)] | None = None
    p: Annotated[Probability | None, COMPUTED]
    q: Annotated[Probability | None, COMPUTED] = None

    @model_validator(mode="after")
    def _check_test(self) -> Self:
        if self.positions != sorted(set(self.positions)):
            raise problem("test_positions", "A test's positions are distinct, in view order")
        if self.p is not None and len(self.positions) < 2:
            raise problem("test_positions", "A test that was computed used two cohorts or more")
        if self.q is not None and self.p is not None and self.q < self.p:
            raise problem("test_q", "A q-value is never below its p-value")
        if "q" in self.model_fields_set and self.p is None:
            raise problem("test_q", "A test that was not computed is not in its family")
        return self


__all__ = [
    "RATIO_MEASURES",
    "RELATIVE_POINTER",
    "ComputedCount",
    "DenominatorDefinition",
    "EffectMeasure",
    "EffectSize",
    "Estimable",
    "ExclusionCounts",
    "HypothesisTest",
    "Interval",
    "NotEstimableReason",
    "Number",
    "Position",
    "Probability",
    "Proportion",
    "RelativePointer",
    "computed_nulls",
    "not_estimable_problems",
]

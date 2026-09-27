"""Charts: Vega-Lite specifications of a result's values (SPEC §8.5; D322).

A chart is written by the server from the result's values after the disclosure pass, never from
anything else: its data is inline (``data.values``), each row copied from a value the result
carries, with the cohort's label, which is data, beside it. It has no transform that computes a
number, no expression, no ``$schema`` and no URL, and it names only the fields it writes itself,
so a client renders it with a CSP-safe interpreter that makes no request (§14). A value that is
``null`` (not estimable, or suppressed) has no row: a chart never draws what the result does not
show.

``compare.existence`` has one chart: a bar per cohort and predicate, the proportion with its
interval as a rule, one row of the facet per predicate, cohorts in view order.

``summary.distribution`` has one chart per column, in parameter order (D328): for categories, a
bar per category, its proportion, one row of the facet per cohort, labelled by its values each
quoted as a JSON string, merged ones joined by ", ", and the row of a category column's
undeclared values under a disclosure setting by ``other values`` unquoted, so that no label is
another's (a category named ``a, b`` or ``other values`` is quoted whole); for numbers, a bar per
histogram bin as the pass left the bins, its count, one row of the facet per cohort, each bin
labelled by its interval (``[0, 30)``, ``(-∞, 0)``, ``[60, 100]``), so that bins merged in one
cohort and not in another are told apart, in order of their edges. A position whose categories or
histogram are suppressed has no row.

``compare.columns`` has one chart per column, in parameter order (D336): for categories, the
bars of each cohort's categories as ``summary.distribution`` draws them; for numbers, each
difference versus the reference, in means and in medians, a point with its interval as a rule,
one row of the facet per measure, one line per other cohort in view order. A difference not shown
has no row, so under a disclosure setting a number's chart has none.

``survival.km`` has one chart (D348): each cohort's curve as a line stepping after each of its
steps from 1 at time 0, its pointwise interval as a band where the curve's steps have both
bounds, one line per cohort in view order; a step whose value is not estimable has no row, and a
cohort with no units none.
"""

import json
import math
from collections.abc import Sequence

from pydantic import JsonValue

from aibi.core.schema.analyses import (
    CategoryDistribution,
    ColumnsValues,
    DistributionValues,
    ExistenceValues,
    HistogramBin,
    NumberComparison,
    NumberDistribution,
    SurvivalValues,
)


def existence_chart(values: ExistenceValues, labels: Sequence[str]) -> dict[str, JsonValue]:
    """The chart of ``compare.existence``'s values, its cohorts labelled in view order."""
    rows: list[JsonValue] = []
    for position, at in enumerate(values.positions):
        for predicate, share in enumerate(at.predicates):
            proportion = share.proportion
            ci = proportion.ci
            low, high = (None, None) if ci is None else (ci.low, ci.high)
            if proportion.estimate is None or low is None or high is None:
                continue
            rows.append(
                {
                    "predicate": predicate,
                    "position": position,
                    "cohort": labels[position],
                    "estimate": proportion.estimate,
                    "low": low,
                    "high": high,
                }
            )
    cohort: dict[str, JsonValue] = {
        "field": "cohort",
        "type": "nominal",
        "sort": None,
        "title": "Cohort",
    }
    return {
        "description": (
            "The proportion of each cohort's units for which each predicate holds, among those "
            "for which it is known, with its interval"
        ),
        "data": {"values": rows},
        "facet": {"row": {"field": "predicate", "type": "ordinal", "title": "Predicate"}},
        "spec": {
            "layer": [
                {
                    "mark": {"type": "bar"},
                    "encoding": {
                        "y": cohort,
                        "x": {
                            "field": "estimate",
                            "type": "quantitative",
                            "scale": {"domain": [0, 1]},
                            "title": "Proportion",
                        },
                    },
                },
                {
                    "mark": {"type": "rule"},
                    "encoding": {
                        "y": cohort,
                        "x": {"field": "low", "type": "quantitative"},
                        "x2": {"field": "high"},
                    },
                },
            ]
        },
    }


def _cohort_row() -> dict[str, JsonValue]:
    return {"field": "cohort", "type": "nominal", "sort": None, "title": "Cohort"}


def distribution_charts(
    values: DistributionValues, labels: Sequence[str]
) -> list[dict[str, JsonValue]]:
    """The charts of ``summary.distribution``'s values, one per column, its cohorts labelled
    in view order."""
    columns = len(values.positions[0].columns)
    return [_column_chart(values, labels, column) for column in range(columns)]


def _category_rows(
    found: CategoryDistribution, position: int, labels: Sequence[str]
) -> list[JsonValue]:
    rows: list[JsonValue] = []
    for share in found.categories or []:
        estimate = share.proportion.estimate
        if estimate is not None:
            rows.append(
                {
                    "position": position,
                    "cohort": labels[position],
                    "category": ", ".join(
                        [
                            *(json.dumps(value.data, ensure_ascii=False) for value in share.values),
                            *(["other values"] if share.other_values else []),
                        ]
                    ),
                    "estimate": estimate,
                }
            )
    return rows


def _category_chart(rows: list[JsonValue], column: int) -> dict[str, JsonValue]:
    return {
        "description": (
            f"Column {column}: the proportion of each cohort's units in each category, among "
            "those with a value"
        ),
        "data": {"values": rows},
        "facet": {"row": _cohort_row()},
        "spec": {
            "mark": {"type": "bar"},
            "encoding": {
                "y": {"field": "category", "type": "nominal", "sort": None, "title": "Category"},
                "x": {
                    "field": "estimate",
                    "type": "quantitative",
                    "scale": {"domain": [0, 1]},
                    "title": "Proportion",
                },
            },
        },
    }


def _column_chart(
    values: DistributionValues, labels: Sequence[str], column: int
) -> dict[str, JsonValue]:
    rows: list[JsonValue] = []
    intervals: dict[tuple[float, float], str] = {}
    numbers = isinstance(values.positions[0].columns[column], NumberDistribution)
    for position, at in enumerate(values.positions):
        found = at.columns[column]
        if isinstance(found, CategoryDistribution):
            rows += _category_rows(found, position, labels)
        elif found.histogram is not None:
            for one in found.histogram.bins:
                bounds = (
                    -math.inf if one.low is None else one.low,
                    math.inf if one.high is None else one.high,
                )
                intervals.setdefault(bounds, _interval(one))
                rows.append(
                    {
                        "position": position,
                        "cohort": labels[position],
                        "bin": intervals[bounds],
                        "low": one.low,
                        "high": one.high,
                        "count": one.count,
                    }
                )
    if numbers:
        return {
            "description": (
                f"Column {column}: each cohort's units per histogram bin, the bins by their "
                "intervals from the lowest"
            ),
            "data": {"values": rows},
            "facet": {"row": _cohort_row()},
            "spec": {
                "mark": {"type": "bar"},
                "encoding": {
                    "x": {
                        "field": "bin",
                        "type": "ordinal",
                        "sort": [intervals[bounds] for bounds in sorted(intervals)],
                        "title": "Bin",
                    },
                    "y": {"field": "count", "type": "quantitative", "title": "Units"},
                    "tooltip": [
                        {"field": "low", "type": "quantitative", "title": "From"},
                        {"field": "high", "type": "quantitative", "title": "To"},
                        {"field": "count", "type": "quantitative", "title": "Units"},
                    ],
                },
            },
        }
    return _category_chart(rows, column)


def columns_charts(values: ColumnsValues, labels: Sequence[str]) -> list[dict[str, JsonValue]]:
    """The charts of ``compare.columns``' values, one per column, its cohorts labelled in view
    order (module docstring)."""
    found: list[dict[str, JsonValue]] = []
    for column, compared in enumerate(values.view.columns):
        if isinstance(compared, NumberComparison):
            found.append(_effects_chart(compared, labels, column))
            continue
        rows: list[JsonValue] = []
        for position, at in enumerate(values.positions):
            one = at.columns[column]
            assert isinstance(one, CategoryDistribution), "a column is of one kind throughout"
            rows += _category_rows(one, position, labels)
        found.append(_category_chart(rows, column))
    return found


def _effects_chart(
    compared: NumberComparison, labels: Sequence[str], column: int
) -> dict[str, JsonValue]:
    rows: list[JsonValue] = []
    for effect in compared.effects:
        ci = effect.ci
        low, high = (None, None) if ci is None else (ci.low, ci.high)
        if effect.estimate is None or low is None or high is None:
            continue
        rows.append(
            {
                "measure": effect.measure.value,
                "position": effect.position,
                "cohort": labels[effect.position],
                "estimate": effect.estimate,
                "low": low,
                "high": high,
            }
        )
    cohort = _cohort_row()
    return {
        "description": (
            f"Column {column}: each cohort's difference in means and in medians versus the "
            "reference, with its interval"
        ),
        "data": {"values": rows},
        "facet": {"row": {"field": "measure", "type": "nominal", "title": "Difference"}},
        "spec": {
            "layer": [
                {
                    "mark": {"type": "point"},
                    "encoding": {
                        "y": cohort,
                        "x": {"field": "estimate", "type": "quantitative", "title": "Difference"},
                    },
                },
                {
                    "mark": {"type": "rule"},
                    "encoding": {
                        "y": cohort,
                        "x": {"field": "low", "type": "quantitative"},
                        "x2": {"field": "high"},
                    },
                },
            ]
        },
    }


def survival_chart(values: SurvivalValues, labels: Sequence[str]) -> dict[str, JsonValue]:
    """The chart of ``survival.km``'s values, its cohorts labelled in view order (module
    docstring): each cohort with units from 1 at time 0, where every curve starts."""
    curve: list[JsonValue] = []
    band: list[JsonValue] = []
    for position, at in enumerate(values.positions):
        if at.last_follow_up is not None:
            start: dict[str, JsonValue] = {
                "position": position,
                "cohort": labels[position],
                "time": 0,
                "survival": 1,
            }
            curve.append(start)
            band.append({**start, "low": 1, "high": 1})
        for step in at.curve.steps:
            if step.survival is None:
                continue
            row: dict[str, JsonValue] = {
                "position": position,
                "cohort": labels[position],
                "time": step.time,
                "survival": step.survival,
            }
            curve.append(row)
            if step.ci.low is not None and step.ci.high is not None:
                band.append({**row, "low": step.ci.low, "high": step.ci.high})
    cohort: dict[str, JsonValue] = {
        "field": "cohort",
        "type": "nominal",
        "sort": None,
        "title": "Cohort",
    }
    time: dict[str, JsonValue] = {"field": "time", "type": "quantitative", "title": "Time"}
    return {
        "description": (
            "Each cohort's Kaplan-Meier curve, with its pointwise interval where both bounds "
            "are shown"
        ),
        "layer": [
            {
                "data": {"values": band},
                "mark": {"type": "area", "interpolate": "step-after", "opacity": 0.2},
                "encoding": {
                    "x": time,
                    "y": {"field": "low", "type": "quantitative"},
                    "y2": {"field": "high"},
                    "color": cohort,
                },
            },
            {
                "data": {"values": curve},
                "mark": {"type": "line", "interpolate": "step-after"},
                "encoding": {
                    "x": time,
                    "y": {
                        "field": "survival",
                        "type": "quantitative",
                        "title": "Survival",
                        "scale": {"domain": [0, 1]},
                    },
                    "color": cohort,
                },
            },
        ],
    }


def _interval(one: HistogramBin) -> str:
    """A bin's interval as text: ``[`` or ``(`` by whether it holds its low edge, ``-∞`` for none,
    and ``]`` or ``)`` likewise at its high edge, ``∞`` for none."""
    low = "-∞" if one.low is None else _edge(one.low)
    high = "∞" if one.high is None else _edge(one.high)
    return f"{'[' if one.includes_low else '('}{low}, {high}{']' if one.includes_high else ')'}"


def _edge(value: float) -> str:
    """An edge as JSON writes it, a whole number without its ``.0``."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 2**53:
        return str(int(value))
    return json.dumps(value)


__all__ = ["columns_charts", "distribution_charts", "existence_chart", "survival_chart"]

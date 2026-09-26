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
"""

import json
import math
from collections.abc import Sequence

from pydantic import JsonValue

from aibi.core.schema.analyses import (
    CategoryDistribution,
    DistributionValues,
    ExistenceValues,
    HistogramBin,
    NumberDistribution,
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


def _column_chart(
    values: DistributionValues, labels: Sequence[str], column: int
) -> dict[str, JsonValue]:
    rows: list[JsonValue] = []
    intervals: dict[tuple[float, float], str] = {}
    numbers = isinstance(values.positions[0].columns[column], NumberDistribution)
    for position, at in enumerate(values.positions):
        found = at.columns[column]
        if isinstance(found, CategoryDistribution):
            for share in found.categories or []:
                estimate = share.proportion.estimate
                if estimate is not None:
                    rows.append(
                        {
                            "position": position,
                            "cohort": labels[position],
                            "category": ", ".join(
                                [
                                    *(
                                        json.dumps(value.data, ensure_ascii=False)
                                        for value in share.values
                                    ),
                                    *(["other values"] if share.other_values else []),
                                ]
                            ),
                            "estimate": estimate,
                        }
                    )
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


__all__ = ["distribution_charts", "existence_chart"]

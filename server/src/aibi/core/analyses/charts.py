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
"""

from collections.abc import Sequence

from pydantic import JsonValue

from aibi.core.schema.analyses import ExistenceValues


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


__all__ = ["existence_chart"]

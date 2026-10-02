"""The disclosure of ``survival.km`` (SPEC §8.4; D351).

Under any disclosure setting, set by the dataset, by the floor or by both, a view of it is
refused, whatever its cohorts' sizes, the refusal naming no value, and applicability calls it
``unavailable``, naming ``min_cell_count``. Why §8.4's grid rule did not protect it is checked
by brute force over whole outputs: over every way n units can end at three times, in an event or
censored, a curve's values alone, at-risk and event counts withheld, pin a count of censorings
below k for every k from 2 to 5, although every event count and every count at risk, all the grid
rule looked at, is at least k."""

import itertools
import json
from collections import defaultdict
from collections.abc import Callable, Iterator
from fractions import Fraction
from typing import Any

import pytest

from aibi.core.analyses.registry import Analyses
from aibi.core.engine import build
from aibi.core.schema.refusals import RefusalCode

Check = Callable[..., Any]
Rows = Callable[..., dict[str, list[dict[str, object]]]]
Shop = Callable[..., Any]

OLD = {"kind": "value", "column": "customers.age", "range": {"gte": 50}}
YOUNG = {"kind": "value", "column": "customers.age", "range": {"lt": 50}}
SETTINGS: list[tuple[dict[str, Any] | None, int | None, str]] = [
    *(({"min_cell_count": k}, None, f"the dataset's min_cell_count, {k}") for k in (2, 5, 11)),
    *((None, k, f"the deployment's floor, {k}") for k in (1, 2, 5, 11)),
    ({"min_cell_count": 3}, 5, "the deployment's floor, 5"),
    ({"min_cell_count": 7}, 5, "the dataset's min_cell_count, 7"),
]
"""Each disclosure setting: the dataset's, the floor and both, and the source each refusal names
with the k it applies (the larger)."""


def document(**params: Any) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "d",
        "unit": "customers",
        "cohorts": {"young": {"all": [YOUNG]}, "old": {"all": [OLD]}},
        "views": [{"analysis": "survival.km", "cohorts": ["young", "old"], "params": params}],
    }


@pytest.mark.parametrize(("disclosure", "floor", "source"), SETTINGS)
@pytest.mark.parametrize("params", [{}, {"grid": [12, 24]}, {"landmarks": [6]}])
def test_a_view_of_it_is_refused_under_any_disclosure_setting(
    check: Check,
    shop: Shop,
    disclosure: dict[str, Any] | None,
    floor: int | None,
    source: str,
    params: dict[str, Any],
) -> None:
    release = shop(survived="delayed", disclosure=disclosure)
    found = check(document(**params), release, floor=floor)
    assert found.views == []
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (RefusalCode.NOT_SUPPORTED, "/views/0/analysis")
    said = "".join(part.model_dump().get("text", "") for part in refusal.message)
    assert f"({source})" in said
    assert all("data" not in part.model_dump() for part in refusal.message)
    assert [part.model_dump()["data"] for part in refusal.alternatives] == [
        "count_cohort",
        "compare.columns",
        "compare.existence",
        "summary.distribution",
    ]


def test_without_a_disclosure_setting_it_is_run(check: Check, shop: Shop) -> None:
    found = check(document(), shop(survived="delayed"))
    assert found.refusals == []
    assert [view.identity.disclosure for view in found.views] == [None]


@pytest.mark.parametrize(
    ("disclosure", "k", "expected"),
    [
        (None, None, ("available", [])),
        (None, 3, ("unavailable", ["min_cell_count"])),
        ({"min_cell_count": 4}, 4, ("unavailable", ["min_cell_count"])),
    ],
)
def test_it_is_unavailable_under_a_disclosure_setting_naming_it(
    shop: Shop, disclosure: dict[str, Any] | None, k: int | None, expected: tuple[str, list[str]]
) -> None:
    release = shop(survived="delayed", disclosure=disclosure)
    for unit in (None, "customers"):
        found = {
            item.analysis: (item.status, item.missing)
            for item in Analyses().applicable(
                list(release.descriptors),
                dataset=release.dataset,
                manifest=release.manifest,
                unit=unit,
                k=k,
            )
        }
        assert found["survival.km"] == expected


def test_it_is_unavailable_without_a_usable_endpoint(shop: Shop) -> None:
    release = shop()
    found = {
        item.analysis: (item.status, item.missing)
        for item in Analyses().applicable(
            list(release.descriptors), dataset=release.dataset, manifest=release.manifest
        )
    }
    assert found["survival.km"] == ("unavailable", ["endpoint"])


@pytest.mark.parametrize("member", ["table", "time_column", "status_column", "event_coding"])
def test_an_endpoint_missing_a_field_it_needs_is_not_usable_or_chosen(
    check: Check, shop: Shop, rows: Rows, member: str
) -> None:
    fields: dict[str, Any] = {
        "table": "customers",
        "time_column": "tenure",
        "status_column": "left",
        "event_coding": {"event": ["yes"], "censored": ["no"]},
        "entry": "at_origin",
    }
    del fields[member]
    lacking = build.descriptor("endpoint", "ep:retention", fields)
    given = shop(survived="origin")
    descriptors = [d for d in given.descriptors if d.id != "ep:retention"]
    release = build.release([*descriptors, lacking], rows(survived=True))
    found = {
        item.analysis: (item.status, item.missing)
        for item in Analyses().applicable(
            list(release.descriptors), dataset=release.dataset, manifest=release.manifest
        )
    }
    assert found["survival.km"] == ("unavailable", ["endpoint"])
    checked = check(document(), release)
    assert checked.views == []
    assert [(r.code, r.path) for r in checked.refusals] == [
        ("MISSING_MEMBER", "/views/0/params/endpoint")
    ]


# --- Why: a curve's values pin its censorings (D351) -----------------------------------------

Split = tuple[int, int, int, int, int, int]
"""The events and censorings at times 1, 2 and 3: (e₁, c₁, e₂, c₂, e₃, c₃)."""


def splits(n: int) -> Iterator[Split]:
    for found in itertools.product(range(n + 1), repeat=5):
        rest = n - sum(found)
        if rest >= 0:
            a, b, c, d, e = found
            yield (a, b, c, d, e, rest)


def curve(n: int, split: Split) -> tuple[tuple[Fraction, ...], tuple[int, ...], tuple[int, ...]]:
    """The Kaplan–Meier curve at times 1, 2 and 3, exactly, with the units at risk and the
    events at each: a unit censored at a time is at risk there."""
    survival = Fraction(1)
    values: list[Fraction] = []
    at_risk: list[int] = []
    events: list[int] = []
    left = n
    for time in range(3):
        happened, censored = split[2 * time], split[2 * time + 1]
        at_risk.append(left)
        events.append(happened)
        if left:
            survival *= Fraction(left - happened, left)
        values.append(survival)
        left -= happened + censored
    return tuple(values), tuple(at_risk), tuple(events)


def pinned(k: int, n: int) -> list[tuple[Split, int]]:
    """The splits of n units whose curve's values, with n, pin c₁ at a count from 1 to k − 1,
    every event and at-risk count at least k: each with the count its values pin."""
    by_values: dict[tuple[Fraction, ...], set[int]] = defaultdict(set)
    passing: list[tuple[Split, tuple[Fraction, ...]]] = []
    for split in splits(n):
        values, at_risk, events = curve(n, split)
        by_values[values].add(split[1])
        if min(at_risk) >= k and min(events) >= k:
            passing.append((split, values))
    found: list[tuple[Split, int]] = []
    for split, values in passing:
        censored = by_values[values]
        if len(censored) == 1 and 0 < split[1] < k:
            found.append((split, split[1]))
    return found


@pytest.mark.parametrize("k", [2, 3, 4, 5])
def test_a_curve_s_values_alone_pin_a_censored_count_below_k_that_every_shown_count_passes(
    k: int,
) -> None:
    found = [one for n in range(3 * k + 1, 4 * k + 3) for one in pinned(k, n)]
    assert found, json.dumps(k)


def test_the_worked_example_of_d351_holds() -> None:
    split = (5, 1, 5, 0, 6, 0)
    values, at_risk, events = curve(17, split)
    assert values[:2] == (Fraction(12, 17), Fraction(72, 187))
    assert (at_risk, events) == ((17, 11, 6), (5, 5, 6))
    same = [other for other in splits(17) if curve(17, other)[0][:2] == values[:2]]
    assert {other[1] for other in same} == {1}

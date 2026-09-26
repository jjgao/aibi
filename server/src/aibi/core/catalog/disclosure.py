"""Catalogue statistics as they are served: the disclosure pass and the references (SPEC §8.1,
§8.4, D271, D272).

The statistics a build counted (``store.statistics``, D270) are disclosed when they are served,
under the effective *k*: the largest of the deployment's floor and the dataset's
``min_cell_count`` (``effective_k``). With *k* set:

- **Observation-state counts** are a linked set with the table's ``n_rows`` (§8.4): a count from 1
  to *k* − 1 is suppressed, and when exactly one of a column's states is, so is the smallest
  non-zero other, the first listed on a tie (``PRESENT``, ``NOT_APPLICABLE``, ``NOT_ASSESSED``,
  ``UNKNOWN``). ``n_rows`` is suppressed only when it is from 1 to *k* − 1 itself, and then every
  state count of the table with it, since a breakdown is ``null`` whenever its total is: it is
  never the smallest other, since a column's states sum to it, so it exceeds every other count of
  a set whose one suppressed count is at least 1.
- **Distributions** are disclosed only while their PRESENT count is shown: a distribution whose
  PRESENT count is suppressed is suppressed whole (``suppressed``), so that no category count or
  bin shows what the suppressed total hid (D271, stricter than §8.4's linked set, which would
  suppress only the smallest other). Categories with a count from 1 to *k* − 1 are pooled into one
  row (``pooled``); when that row still has a count from 1 to *k* − 1, the distribution is
  suppressed. A list column's rows count under each of their values, so its pooled row cannot be
  summed without counting a row twice: its count is ``null`` (suppressed) whenever categories were
  pooled. Histograms take their edges from the declared range; one whose edges came from the
  data is not reported (``no_declared_range``). Bins with 1 to *k* − 1 values are merged as §8.4
  says, the smallest first (the leftmost on a tie), with the nearest non-empty bin on the side
  whose neighbour holds fewer values (the left on a tie), empty bins between them included, until
  none is left or one non-empty bin remains; one that still holds 1 to *k* − 1 suppresses the
  distribution. Minima and maxima are not reported.
- **Values an output cannot carry**: categories whose listed values are not all Unicode text of
  at most ``MAX_TEXT`` characters with a reference of at most ``MAX_POINTER`` characters of
  pointer (a value holding a noncharacter, or a very long one) are not reported, with or without
  *k* (``unrepresentable``, D276).

Whatever was suppressed, pooled or merged makes the output carry ``SUPPRESSED``. Without *k*
every count is shown as counted.

**References** (D272) point into the disclosed statistics of a descriptor:
``stat:<manifest>/<table>/n_rows``; for a column, ``/states/<STATE>``, ``/categories/<value>``,
``/pooled``, ``/bins/<i>`` (by position among the disclosed bins), ``/min`` and ``/max``; and for
the curation queue's counts, ``stat:<manifest>/dataset/report/<i>``, the *i*-th note of the import
report. ``?floor=<n>`` is appended whenever the deployment sets a floor, whichever setting is the
larger, so that a reference names its numbers by its own text.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue

from aibi.core.schema.catalog import (
    Bin,
    CategoriesOut,
    CategoryCount,
    ColumnStatistics,
    Distribution,
    HistogramOut,
    NoDistribution,
    NoneReason,
    StatCount,
    StateCounts,
    StatValue,
)
from aibi.core.schema.jsonio import escape_token, is_text
from aibi.core.schema.limits import MAX_POINTER, MAX_TEXT
from aibi.core.schema.numbers import NotEstimableReason
from aibi.core.schema.output import Data
from aibi.core.schema.results import stat_reference
from aibi.core.schema.semantics import ObservationState
from aibi.core.store.statistics import STATES, TableStatistics

_SUPPRESSED = {"/count": NotEstimableReason.SUPPRESSED}


def effective_k(floor: int | None, *settings: int | None) -> int | None:
    """The largest of the floor and the dataset's ``min_cell_count`` settings (§8.4): its
    release's, and for a draft the latest published release's too (D275)."""
    given = [k for k in (floor, *settings) if k is not None]
    return max(given) if given else None


def _small(k: int | None, count: int) -> bool:
    return k is not None and 1 <= count <= k - 1


@dataclass(frozen=True)
class References:
    """The references of one release's statistics under one floor."""

    manifest: str
    floor: int | None

    def of(self, descriptor: str, *tokens: str | int) -> str:
        pointer = "".join("/" + escape_token(token) for token in tokens)
        return stat_reference(self.manifest, descriptor, pointer, self.floor)

    def count(self, count: int | None, descriptor: str, *tokens: str | int) -> StatCount:
        reference = self.of(descriptor, *tokens)
        if count is None:
            return StatCount(count=None, reference=reference, not_estimable=_SUPPRESSED)
        return StatCount(count=count, reference=reference)


@dataclass(frozen=True)
class DisclosedTable:
    """A table's statistics after the disclosure pass: counts ``None`` where suppressed."""

    n_rows: int | None
    states: Mapping[str, Mapping[str, int | None]]
    distributions: Mapping[str, Mapping[str, JsonValue]]
    suppressed: bool = False
    pooled: frozenset[str] = frozenset()
    """Columns whose categories were pooled or bins merged."""


@dataclass(frozen=True)
class _Shown:
    """A distribution as disclosed, and whether it suppressed, pooled or merged anything."""

    distribution: dict[str, JsonValue]
    suppressed: bool = False
    pooled: bool = False


def disclose_table(raw: TableStatistics, k: int | None) -> DisclosedTable:
    """The disclosure pass over one table's statistics (module docstring)."""
    rows = cast(int, raw["n_rows"])
    columns = cast(dict[str, dict[str, JsonValue]], raw["columns"])
    counted = {
        name: {
            state.value: cast(int, cast(dict[str, JsonValue], c["states"])[state.value])
            for state in STATES
        }
        for name, c in columns.items()
    }
    rows_hidden = _small(k, rows)
    hidden: dict[str, set[str]] = {
        name: {state for state, count in states.items() if _small(k, count)}
        for name, states in counted.items()
    }
    if not rows_hidden:
        for name, states in counted.items():
            if len(hidden[name]) == 1:
                others = [
                    (count, position, state)
                    for position, (state, count) in enumerate(states.items())
                    if state not in hidden[name] and count > 0
                ]
                hidden[name].add(min(others)[2])
    states_out = {
        name: {
            state: None if rows_hidden or state in hidden[name] else count
            for state, count in states.items()
        }
        for name, states in counted.items()
    }
    shown = {
        name: _distribution(
            cast(dict[str, JsonValue], column["distribution"]),
            states_out[name][ObservationState.PRESENT.value],
            k,
        )
        for name, column in columns.items()
    }
    return DisclosedTable(
        n_rows=None if rows_hidden else rows,
        states=states_out,
        distributions={name: found.distribution for name, found in shown.items()},
        suppressed=rows_hidden
        or any(hidden.values())
        or any(found.suppressed for found in shown.values()),
        pooled=frozenset(name for name, found in shown.items() if found.pooled),
    )


def _none(reason: NoneReason) -> dict[str, JsonValue]:
    return {"kind": "none", "reason": reason}


def _representable(value: JsonValue) -> bool:
    """Whether an output can carry a category value and its reference (module docstring)."""
    if not isinstance(value, str):
        return True
    pointer = len("/categories/") + len(escape_token(value))
    return len(value) <= MAX_TEXT and pointer <= MAX_POINTER and is_text(value)


def _distribution(raw: dict[str, JsonValue], present: int | None, k: int | None) -> _Shown:
    kind = raw["kind"]
    if kind == "none":
        return _Shown(raw)
    if kind == "categories" and k is None:
        listed = cast(list[list[JsonValue]], raw["categories"])
        if not all(_representable(entry[0]) for entry in listed):
            return _Shown(_none("unrepresentable"))
    if k is None:
        return _Shown(raw)
    if present is None:
        return _Shown(_none("suppressed"), suppressed=True)
    if kind == "categories":
        return _pooled(raw, k)
    if raw["from"] == "data":
        return _Shown(_none("no_declared_range"))
    return _merged(raw, k)


def _pooled(raw: dict[str, JsonValue], k: int) -> _Shown:
    categories = cast(list[list[JsonValue]], raw["categories"])
    kept = [entry for entry in categories if not _small(k, cast(int, entry[1]))]
    pooled = [cast(int, entry[1]) for entry in categories if _small(k, cast(int, entry[1]))]
    if not all(_representable(entry[0]) for entry in kept):
        return _Shown(_none("unrepresentable"))
    found: dict[str, JsonValue] = {
        "kind": "categories",
        "categories": cast(JsonValue, kept),
        "multi_membership": raw["multi_membership"],
    }
    if not pooled:
        return _Shown(found)
    if raw["multi_membership"]:
        found["pooled"] = None
    else:
        total = sum(pooled)
        if _small(k, total):
            return _Shown(_none("suppressed"), suppressed=True, pooled=True)
        found["pooled"] = total
    return _Shown(found, suppressed=True, pooled=True)


def _merged(raw: dict[str, JsonValue], k: int) -> _Shown:
    bins = _raw_bins(raw)
    merged = False
    while True:
        small = [
            (cast(int, b["count"]), i)
            for i, b in enumerate(bins)
            if _small(k, cast(int, b["count"]))
        ]
        filled = [i for i, b in enumerate(bins) if cast(int, b["count"]) > 0]
        if not small or len(filled) <= 1:
            break
        _, at = min(small)
        left = max((i for i in filled if i < at), default=None)
        right = min((i for i in filled if i > at), default=None)
        if left is None or (
            right is not None and cast(int, bins[right]["count"]) < cast(int, bins[left]["count"])
        ):
            start, end = at, cast(int, right)
        else:
            start, end = left, at
        span = bins[start : end + 1]
        joined: dict[str, JsonValue] = {
            "low": span[0]["low"],
            "high": span[-1]["high"],
            "includes_low": span[0]["includes_low"],
            "includes_high": span[-1]["includes_high"],
            "count": sum(cast(int, b["count"]) for b in span),
        }
        bins[start : end + 1] = [joined]
        merged = True
    if any(_small(k, cast(int, b["count"])) for b in bins):
        return _Shown(_none("suppressed"), suppressed=True, pooled=merged)
    shown: dict[str, JsonValue] = {
        "kind": "histogram",
        "from": raw["from"],
        "bins": cast(JsonValue, bins),
    }
    return _Shown(shown, suppressed=merged, pooled=merged)


def _value(value: JsonValue) -> float | Data:
    if isinstance(value, str):
        return Data(data=value)
    return cast(float, value)


def column_output(
    table: str,
    column: str,
    disclosed: DisclosedTable,
    references: References,
) -> ColumnStatistics:
    """A column's statistics as a tool gives them, every count with its reference."""
    descriptor = f"{table}.{column}"
    states = disclosed.states[column]
    counts = {
        state: references.count(states[state], descriptor, "states", state) for state in states
    }
    return ColumnStatistics(
        rows=references.count(disclosed.n_rows, table, "n_rows"),
        states=StateCounts.model_validate(counts),
        distribution=_distribution_output(descriptor, disclosed.distributions[column], references),
    )


def _distribution_output(
    descriptor: str, found: Mapping[str, JsonValue], references: References
) -> Distribution:
    kind = found["kind"]
    if kind == "none":
        return NoDistribution(kind="none", reason=cast(NoneReason, found["reason"]))
    if kind == "categories":
        categories = cast(Sequence[Sequence[JsonValue]], found["categories"])
        listed = [
            CategoryCount(
                value=Data(data=cast(str, value)),
                count=cast(int, count),
                reference=references.of(descriptor, "categories", cast(str, value)),
            )
            for value, count in categories
        ]
        pooled = (
            references.count(cast(int | None, found["pooled"]), descriptor, "pooled")
            if "pooled" in found
            else None
        )
        return CategoriesOut(
            kind="categories",
            categories=listed,
            pooled=pooled,
            multi_membership=cast(bool, found["multi_membership"]),
        )
    bins_given = found.get("bins")
    if bins_given is None:
        bins_given = _raw_bins(found)
    bins = [
        Bin(
            low=None if b["low"] is None else _value(b["low"]),
            high=None if b["high"] is None else _value(b["high"]),
            includes_low=cast(bool, b["includes_low"]),
            includes_high=cast(bool, b["includes_high"]),
            count=cast(int, b["count"]),
            reference=references.of(descriptor, "bins", index),
        )
        for index, b in enumerate(cast(list[dict[str, JsonValue]], bins_given))
    ]
    extremes = {
        name: StatValue(value=_value(found[name]), reference=references.of(descriptor, name))
        for name in ("min", "max")
        if name in found
    }
    return HistogramOut(
        kind="histogram",
        edges_from=cast(str, found["from"]),  # pyright: ignore[reportArgumentType]
        bins=bins,
        min=extremes.get("min"),
        max=extremes.get("max"),
    )


def _raw_bins(found: Mapping[str, JsonValue]) -> list[dict[str, JsonValue]]:
    edges = cast(list[JsonValue], found["edges"])
    counts = cast(list[int], found["counts"])
    last = len(edges) - 1
    return [
        {
            "low": None if i == 0 else edges[i - 1],
            "high": None if i == last + 1 else edges[i],
            "includes_low": 0 < i <= last,
            "includes_high": i == last,
            "count": count,
        }
        for i, count in enumerate(counts)
    ]


__all__ = [
    "DisclosedTable",
    "References",
    "column_output",
    "disclose_table",
    "effective_k",
]

"""Measures applicability on large releases and registries (D420); run by hand, never by pytest
(its name is not a test's), from ``server/``:

    uv run python -m tests.core.analyses.measure_applicable std     # 50, 200, 1,000 tables
    uv run python -m tests.core.analyses.measure_applicable scan    # the old scan, 50 tables
    uv run python -m tests.core.analyses.measure_applicable memory  # 20,000 units

It records, and asserts nothing of, the seconds per ``Analyses.applicable`` call (the least and
the most of three) on releases of 50, 200 and 1,000 tables of 40 columns each, for the core's
registry alone and for packs at the cap: 4,096 requirements over 1,000 analyses in 16 packs,
either drawn as a pack might (``plain``) or each of its own ``min`` over every requirement
shape, with requirement predicates that hold in every pack (``distinct``), the adversary of
the index; under *k* unset and 5. Also the copies of every entry that ``Analyses.all`` makes,
once for ``resources/list`` and ``describe_dataset``, twice for ``list_analyses`` with a
dataset (with its entries' conversion), and the peak memory of one call at 20,000 units. No
domain: tables and columns are letters and numbers.
"""

import gc
import random
import sys
import time
import tracemalloc
from collections.abc import Callable
from typing import Any

from tests.core.analyses.applicability_oracle import (
    _DATATYPES,
    _ON,
    DATATYPES,
    QuickScan,
    predicate,
    registry_of,
)

from aibi.core.analyses.registry import Analyses
from aibi.core.engine import build
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.entries import analysis_entry
from aibi.core.schema.pack_api import PackRegistry

SHAPES: list[dict[str, Any]] = [
    {"kind": kind, **({"on": on} if on else {}), **({"datatype": datatype} if datatype else {})}
    for kind in ("endpoint", "column", "table")
    for on in _ON
    for datatype in (_DATATYPES if kind == "column" else (None,))
]
"""Every requirement shape the index tells apart (23)."""


def release(tables: int, columns: int = 40, seed: int = 0) -> list[Descriptor]:
    """``tables`` keyed tables of ``columns`` columns of every datatype (one in ten
    proposed), every 17th a coverage table, each but the first related to the one before,
    an endpoint on every fifth (every tenth with its entry, every fifteenth unusable)."""
    chosen = random.Random(seed)
    found: list[Descriptor] = [build.dataset()]
    for number in range(tables):
        name = f"t{number:05d}"
        role = "coverage" if number % 17 == 16 else ("entity" if number % 3 else "event")
        found.append(build.table(name, ["c00"], role=role))
        for column in range(columns):
            datatype = DATATYPES[chosen.randrange(len(DATATYPES))] if column else "string"
            status = "proposed" if chosen.random() < 0.1 else "asserted"
            extra: dict[str, Any] = {}
            if datatype in ("category", "list<category>"):
                extra["permissible_values"] = {"values": [{"value": "a"}, {"value": "b"}]}
            found.append(build.column(f"{name}.c{column:02d}", datatype, status=status, **extra))
        if number:
            found.append(build.relationship(name, ["c00"], f"t{number - 1:05d}", role=f"r{number}"))
        if number % 5 == 0:
            fields: dict[str, Any] = {
                "table": name,
                "time_column": "c01",
                "status_column": "c02",
                "event_coding": {"event": ["a"], "censored": ["b"]},
            }
            if number % 10 == 0:
                fields["entry"] = "at_origin"
            if number % 15 == 0:
                del fields["event_coding"]
            found.append(build.descriptor("endpoint", f"ep:e{number}", fields))
    return found


def plain(chosen: random.Random, number: int) -> dict[str, Any]:
    """A requirement as a pack might write one."""
    found: dict[str, Any] = {}
    kind = chosen.choice(["endpoint", "column", "table", None])
    if kind is not None:
        found["kind"] = kind
    if chosen.random() < 0.6:
        found["on"] = "unit"
    if chosen.random() < 0.6:
        found["datatype"] = chosen.choice(DATATYPES)
    if chosen.random() < 0.7:
        found["min"] = chosen.choice([0, 1, 2, 3, 5, 40])
    return found


def distinct(chosen: random.Random, number: int) -> dict[str, Any]:
    """Each requirement its own ``min``, the shapes in turn: no two signatures alike."""
    return {**SHAPES[number % len(SHAPES)], "min": number + 1}


def at_cap(make: Callable[[random.Random, int], dict[str, Any]], predicates: bool) -> PackRegistry:
    """16 packs of 63 or 62 analyses (1,000, ``MAX_PACK_ANALYSES``; D420 measured 1,008, before
    D421 capped them), 4,096 requirements in all (96 analyses with 5, the rest with 4); with
    ``predicates``, each analysis's first requirement also names one of its pack's four
    requirement predicates, which hold (64 called per call)."""
    chosen = random.Random(7)
    given: dict[str, list[list[dict[str, Any]]]] = {}
    held: dict[str, dict[str, Callable[[object], bool]]] = {}
    number = 0
    count = 0
    for position in range(16):
        pack = f"p{position:02d}"
        analyses: list[list[dict[str, Any]]] = []
        for analysis in range(63 if position < 8 else 62):
            count += 1
            requires: list[dict[str, Any]] = []
            for role in range(5 if count <= 96 else 4):
                one = {"role": f"r{role}", **make(chosen, number)}
                if predicates and role == 0:
                    one["predicate"] = f"{pack}.h{analysis % 4}"
                requires.append(one)
                number += 1
            analyses.append(requires)
        given[pack] = analyses
        held[pack] = {f"h{one}": predicate(f"{pack}.h{one}", True) for one in range(4)}
    assert (number, count) == (4_096, 1_000)
    return registry_of(given, held if predicates else None)


def timed(run: Callable[[], object], times: int = 3) -> str:
    found: list[float] = []
    for _ in range(times):
        gc.collect()
        started = time.perf_counter()
        run()
        found.append(time.perf_counter() - started)
    return f"{min(found):.2f}-{max(found):.2f} s"


def standard() -> None:
    registries: dict[str, PackRegistry | None] = {
        "core": None,
        "plain": at_cap(plain, False),
        "distinct, predicates": at_cap(distinct, True),
    }
    for name, packs in registries.items():
        analyses = Analyses(packs)
        print(
            f"{name}: Analyses.all() {timed(analyses.all)}; with entries "
            f"{timed(lambda a=analyses: [analysis_entry(one.entry) for one in a.all()])}",
            flush=True,
        )
    for tables in (50, 200, 1_000):
        descriptors = release(tables)
        for name, packs in registries.items():
            for k in (None, 5):
                figure = timed(
                    lambda p=packs, d=descriptors, k=k: Analyses(p).applicable(
                        d, dataset="d", manifest="m", k=k
                    )
                )
                print(
                    f"{tables} x 40 ({len(descriptors)} descriptors) {name}, k={k}: {figure}",
                    flush=True,
                )


def scan() -> None:
    descriptors = release(50)
    for name, packs in (("core", None), ("distinct, predicates", at_cap(distinct, True))):
        for k in (None, 5):
            figure = timed(
                lambda p=packs, k=k: QuickScan(p).applicable(
                    descriptors, dataset="d", manifest="m", k=k
                ),
                1,
            )
            print(f"scan, 50 x 40, {name}, k={k}: {figure}", flush=True)


def memory(units: int = 20_000) -> None:
    descriptors: list[Descriptor] = [build.dataset()]
    for number in range(units):
        name = f"t{number:05d}"
        descriptors.append(build.table(name, ["c0"]))
        for column in range(1 + number % 7):
            datatype = DATATYPES[(number + column) % len(DATATYPES)] if column else "integer"
            extra: dict[str, Any] = {}
            if datatype in ("category", "list<category>"):
                extra["permissible_values"] = {"values": [{"value": "a"}]}
            descriptors.append(build.column(f"{name}.c{column}", datatype, **extra))
    packs = at_cap(distinct, True)
    analyses = Analyses(packs)
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    analyses.applicable(descriptors, dataset="d", manifest="m")
    elapsed = time.perf_counter() - started
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    figure = timed(lambda: analyses.applicable(descriptors, dataset="d", manifest="m"), 1)
    print(
        f"{units} units ({len(descriptors)} descriptors), distinct, predicates, k=None: "
        f"peak {peak / 2**20:.0f} MiB ({elapsed:.2f} s traced), {figure} untraced",
        flush=True,
    )


if __name__ == "__main__":
    {"std": standard, "scan": scan, "memory": memory}[sys.argv[1] if len(sys.argv) > 1 else "std"]()

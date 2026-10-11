"""Measures what serving a registry at the served-form caps costs (D421); run by hand, never by
pytest (its name is not a test's), from ``server/``:

    uv run python -m tests.core.api.measure_served [a part of the runs' names]

It records, and asserts nothing of, for the core alone and for registries at the caps: model-
dense entries at ``MAX_SERVED_VALUES``; for each listing zeros at the value cap, floats and ASCII,
control and astral text at ``MAX_SERVED_BYTES``, and every container shape (empty objects and
arrays, pairs, objects of keys, objects and arrays 8, 30 and 60 deep) at
``MAX_SERVED_CONTAINERS`` with the rest of the value cap zeros; and 1,000 leaf kinds: the seconds
of registration and of the loader's check (``served_problems``), and per call (the least of
three) the thread's time in the service function, the event loop's time dumping the HTTP body
and the MCP result (the tool's ``_result`` and the SDK's dump of the JSON-RPC answer), both real
transports end to end, ``resources/list``, ``Analyses.entries`` against the copies of
``Analyses.all``, and ``applicable`` on a release of 200 keyed tables of 40 columns; and the
served values, containers and bytes. No domain: packs, kinds and analyses are letters and
numbers.
"""

import gc
import json
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal

import mcp.types as types
from tests.core.analyses.measure_applicable import release
from tests.core.api.served_builders import Spec, pack_of, spread, transports

from aibi.core.analyses.registry import Analyses
from aibi.core.catalog.served import Measured, measured, served_problems
from aibi.core.catalog.service import Catalog
from aibi.core.mcp.server import _result  # pyright: ignore[reportPrivateUsage]
from aibi.core.schema.catalog import ListAnalyses, ListLeafKinds
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.entries import analysis_entry
from aibi.core.schema.limits import MAX_SERVED_CONTAINERS, MAX_SERVED_VALUES
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.store.store import Store


def least(run: Callable[[], object], times: int = 3) -> float:
    found: list[float] = []
    for _ in range(times):
        gc.collect()
        started = time.perf_counter()
        run()
        found.append(time.perf_counter() - started)
    return min(found)


def sdk(result: types.CallToolResult) -> bytes:
    """The JSON-RPC answer as the SDK writes it, at ``id`` 1."""
    answer = types.JSONRPCResponse(
        jsonrpc="2.0", id=1, result=result.model_dump(by_alias=True, mode="json", exclude_none=True)
    )
    return types.JSONRPCMessage(answer).model_dump_json(by_alias=True, exclude_none=True).encode()


def measure(name: str, specs: list[Spec], descriptors: list[Descriptor]) -> None:
    started = time.perf_counter()
    given = [pack_of(spec) for spec in specs]
    built = time.perf_counter() - started
    started = time.perf_counter()
    registry = PackRegistry(given, core_version="0.0.1")
    registered = time.perf_counter() - started
    started = time.perf_counter()
    problems = served_problems(registry)
    checked = time.perf_counter() - started
    forms = measured(registry)
    root = Path(tempfile.mkdtemp())
    store = Store(root / "store")
    catalog = Catalog(store, registry=registry)
    analyses = Analyses(registry)
    lines = [
        f"{name}: packs built {built:.2f} s, registered {registered:.2f} s, checked "
        f"{checked:.2f} s, {'refused' if problems else 'within the caps'}"
    ]
    for form in forms:
        lines.append(
            f"  {form.tool}: {form.values:,} values, {form.containers:,} containers, "
            f"{form.bytes / 2**20:.2f} MiB over MCP"
        )
    for tool, call in (
        ("list_analyses", lambda: catalog.list_analyses(ListAnalyses())),
        ("list_leaf_kinds", lambda: catalog.list_leaf_kinds(ListLeafKinds())),
    ):
        found = call()
        thread = least(call)
        http = least(found.model_dump_json)
        loop = least(lambda found=found: sdk(_result(found)))
        lines.append(
            f"  {tool} per call: thread {thread:.3f} s, loop HTTP {http:.3f} s, loop MCP "
            f"{loop:.3f} s"
        )
    with transports(root / "app", registry) as call:
        for tool in ("list_analyses", "list_leaf_kinds"):
            ends = least(lambda tool=tool: call(tool, {}))
            lines.append(f"  {tool} both transports end to end: {ends:.3f} s")
    copies = least(lambda: [analysis_entry(a.entry) for a in analyses.all()])
    applicable = least(lambda: analyses.applicable(descriptors, dataset="d", manifest="m"), 1)
    lines.append(
        f"  resources/list {least(catalog.resources):.3f} s; entries {least(analyses.entries):.3f}"
        f" s against the copies {copies:.3f} s; applicable at 200 tables {applicable:.3f} s"
    )
    store.close()
    print("\n".join(lines), flush=True)


MODEL_DENSE = spread(1000, requires=4, extra=6, methods=64, assumptions=40, caveats=48)


FLOAT = "0.12345678901234566"
ASTRAL = json.dumps("\U0001f600")
SHAPES: dict[str, tuple[str, int, str]] = {
    "empty objects": ("object", 1, "0"),
    "empty arrays": ("array", 1, "0"),
    "empty arrays, rest floats": ("array", 1, FLOAT),
    "pairs of arrays": ("array", 2, "0"),
    "objects of 4 keys": ("keys", 4, "0"),
    "objects of 9 astral keys": ("astral keys", 9, "0"),
    "objects of 6 keys holding astral strings": ("astral values", 6, "0"),
    "objects of 6 keys holding astral strings, rest astral strings": ("astral values", 6, ASTRAL),
    "objects 8 deep": ("object", 8, "0"),
    "arrays 8 deep": ("array", 8, "0"),
    "objects 30 deep": ("object", 30, "0"),
    "arrays 30 deep": ("array", 30, "0"),
    "objects 60 deep": ("object", 60, "0"),
    "objects 60 deep, rest floats": ("object", 60, FLOAT),
    "objects 60 deep, rest astral strings": ("object", 60, ASTRAL),
    "objects 60 deep under astral keys, rest astral strings": ("astral chain", 60, ASTRAL),
}
"""The container shapes, each taken to the container cap with the rest of the value cap the
filler (zeros, floats or one-astral-character strings), within the byte cap."""


def at_both_caps(
    at: Literal["analysis", "kind"], shape: str, depth: int, filler: str
) -> list[Spec]:
    """One pack whose one analysis's ``params`` or one kind's schema holds ``shape`` items up to
    ``MAX_SERVED_CONTAINERS`` and ``filler`` up to ``MAX_SERVED_VALUES``, or as many as the
    byte cap leaves, by the measure."""
    index = 0 if at == "analysis" else 1
    zero = Spec(id="p00", analyses=int(at == "analysis"), kinds=int(at == "kind"), at=at,
                nests=(("object", 1, 1),), filler=filler)  # fmt: skip
    base = measured(PackRegistry([pack_of(zero)], core_version="0.0.1"))[index]
    room = MAX_SERVED_CONTAINERS - base.containers + 1
    each = depth if shape in ("object", "array", "astral chain") else 1
    nests = ((shape, depth, room // each), ("object", 1, room % each))
    spec = replace(zero, nests=nests)

    def found(count: int) -> Measured:
        given = replace(spec, zeros=count)
        return measured(PackRegistry([pack_of(given)], core_version="0.0.1"))[index]

    first = found(0)
    count = MAX_SERVED_VALUES - first.values
    while count > 0 and found(count).bytes > 4 * 2**20:
        step = found(1_000).bytes - first.bytes
        count -= max(1, (found(count).bytes - 4 * 2**20) * 1_000 // max(step, 1) + 1)
    return [replace(spec, zeros=count)]


def to_the_cap(at: Literal["analysis", "kind"], unit: str, member: str, item: object) -> Spec:
    """One pack whose one analysis's ``params`` or one kind's schema holds as many ``item``s
    (in ``examples``, or ``pad`` text) as bring its listing to the cap of ``unit`` (values or
    bytes), by the measure of one and of two thousand of them."""
    index = 0 if at == "analysis" else 1
    zero = Spec(id="p00", analyses=int(at == "analysis"), kinds=int(at == "kind"), at=at)

    def spec(count: int) -> Spec:
        if member == "pad":
            return replace(zero, pad=str(item) * count)
        return replace(zero, examples=json.dumps([item] * count))

    def size(count: int) -> int:
        found = measured(PackRegistry([pack_of(spec(count))], core_version="0.0.1"))[index]
        return found.values if unit == "values" else found.bytes

    one, two = size(1_000), size(2_000)
    each = (two - one) / 1_000
    most = MAX_SERVED_VALUES if unit == "values" else 4 * 2**20
    count = 1_000 + int((most - one) // each)
    while size(count) > most:
        count -= 1
    return spec(count)


def main(only: str = "") -> None:
    descriptors = release(200)
    runs: list[tuple[str, Callable[[], list[Spec]]]] = [
        ("core alone", list),
        (
            "model-dense at the value cap",
            lambda: [
                Spec(**{**spec.__dict__, "tail": 15_341 // 16 + (1 if n < 15_341 % 16 else 0)})
                for n, spec in enumerate(MODEL_DENSE)
            ],
        ),
    ]
    for at in ("analysis", "kind"):
        listing = "list_analyses" if at == "analysis" else "list_leaf_kinds"
        runs.append(
            (
                f"{listing}: zeros at the value cap",
                lambda at=at: [to_the_cap(at, "values", "examples", 0)],
            )
        )
        runs.append(
            (
                f"{listing}: floats at the byte cap",
                lambda at=at: [to_the_cap(at, "bytes", "examples", 0.12345678901234566)],
            )
        )
        for name, character in (("ASCII", "a"), ("control", "\x01"), ("astral", "\U0001f600")):
            runs.append(
                (
                    f"{listing}: {name} at the byte cap",
                    lambda at=at, c=character: [to_the_cap(at, "bytes", "pad", c)],
                )
            )
        for name, (shape, depth, filler) in SHAPES.items():
            runs.append(
                (
                    f"{listing}: {name} at both caps",
                    lambda at=at, s=shape, d=depth, f=filler: at_both_caps(at, s, d, f),
                )
            )
    runs.append(("1,000 leaf kinds", lambda: spread(1000, kinds=True)))
    for name, specs in runs:
        if only in name:
            measure(name, specs(), descriptors)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")

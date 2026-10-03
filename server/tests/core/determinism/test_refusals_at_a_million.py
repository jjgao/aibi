"""A write that refuses a million references, in bounded time and memory (D405), in D372's job of
a million rows: refusals are recorded and only those returned are built (``finish_lazy``)."""

import gc
import time
import tracemalloc
from typing import Any

import pytest
from pydantic import TypeAdapter

import aibi
from aibi.core.engine import build
from aibi.core.schema.descriptors import ConceptDescriptor, ConceptFields, Descriptor
from aibi.core.schema.limits import MAX_REFUSALS
from aibi.core.schema.pack_api import Pack, PackManifest, PackRegistry
from aibi.core.schema.refusals import Refusal
from aibi.core.store.writes import check_writes

pytestmark = pytest.mark.million

_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)
COUNT = 1_000_000


def _registry() -> PackRegistry:
    """A test-only pack of forty value concepts, so that each refusal lists 16 ids."""
    concepts = [
        ConceptDescriptor(
            kind="concept",
            id=f"shelf:v{index:02d}",
            version=1,
            label="A shelf mark",
            definition="A value concept of the shelf.",
            fields=ConceptFields(sort="value"),
        )
        for index in range(40)
    ]
    manifest = PackManifest(id="shelf", version="1.0.0", results_version=1, requires_core=">=0")
    return PackRegistry([Pack(manifest=manifest, concepts=concepts)], core_version=aibi.__version__)


def _columns(column: Descriptor) -> list[Descriptor]:
    return [
        build.dataset(),
        build.table("members"),
        *(column.model_copy(update={"id": f"members.c{index:07d}"}) for index in range(COUNT)),
    ]


def _refused(written: list[Descriptor], registry: PackRegistry) -> list[Refusal]:
    found = check_writes(written, registry)
    assert len(found) == MAX_REFUSALS + 1
    left: Any = found[-1].message[0].model_dump()
    assert left == {"text": f"{COUNT - MAX_REFUSALS} more refusals were left out"}
    return found


def _refused_within(written: list[Descriptor], seconds: float, mebibytes: float) -> None:
    """``check_writes`` over ``written`` returns in under ``seconds``, and, called again with
    ``tracemalloc`` on (which slows it several times, so the time is of the first call),
    allocates at its peak no more than ``mebibytes`` above what it held when called: the call's
    own allocations, not the process's high-water mark, which the built ``written`` and the
    other tests also set."""
    registry = _registry()
    gc.collect()
    started = time.perf_counter()
    _refused(written, registry)
    elapsed = time.perf_counter() - started
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        _refused(written, registry)
        grown = (tracemalloc.get_traced_memory()[1] - before) / 2**20
    finally:
        tracemalloc.stop()
    print(f"{elapsed:.1f} s, +{grown:.0f} MiB")
    assert elapsed < seconds, elapsed
    assert grown < mebibytes, grown


def test_a_million_stale_concepts_are_refused_in_bounded_time_and_memory() -> None:
    """Each column maps to a concept no installed pack registers, each refusal listing 16 ids:
    measured at 7.5 s and, by ``tracemalloc``, +323 MiB at the peak (the time bound is four
    times as much; the memory bound is 500 MiB, which a record holding a copy of its listing,
    about 370 MiB more, exceeds)."""
    mapping = {"concept": "shelf:gone", "transform": None}
    _refused_within(_columns(build.column("members.c", "string", maps_to=mapping)), 30, 500)


def test_a_million_unregistered_extensions_are_refused_in_bounded_time_and_memory() -> None:
    """Each column carries an extension of a pack neither registered nor listed: measured at
    6.7 s and, by ``tracemalloc``, +300 MiB at the peak (by ``ru_maxrss`` +357 MiB, where
    building every refusal took 36.7 s and +1,966 MiB)."""
    column = build.column("members.c", "string").model_dump(mode="json")
    column["extensions"] = {"zz": {"a": 1}}
    column["curation"]["/extensions/zz/a"] = column["curation"]["/label"]
    _refused_within(_columns(_ADAPTER.validate_python(column)), 30, 500)

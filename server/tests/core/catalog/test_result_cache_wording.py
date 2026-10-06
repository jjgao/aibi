"""The ``RESULT_CACHE`` boundary (SPEC §8.1, D374, D399): a cached result is read back only under
the wording it was written with. A row of another wording, or one written before D399 (which has
none), is a miss the call fills again; it is no fault, so nothing is logged."""

import json
import logging
from typing import Any

import pytest
from tests.core.catalog.test_result_cache import (
    Orchard,
    World,
    catalog_of,
    document,
    run,
)

from aibi.core.analyses import results as results_module
from aibi.core.catalog.cohorts import ENGINE
from aibi.core.schema.output import CACHE_WORDING


def _replaced(world: World, result: str, issuance: str, content: bytes) -> None:
    world.store.results.clear()
    with world.store.db.transaction() as db:
        assert world.store.results.fill(db, result=result, issuance=issuance, content=content)


def _without_wording(content: bytes) -> bytes:
    written: dict[str, Any] = json.loads(content)
    assert written.pop("text_format") == CACHE_WORDING
    return json.dumps(written).encode()


def test_a_row_of_another_wording_is_a_quiet_miss_the_call_replaces(
    world: World,
    orchard: Orchard,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    run(catalog, document())
    monkeypatch.setattr(results_module, "CACHE_WORDING", CACHE_WORDING + 1)
    with caplog.at_level(logging.ERROR):
        [again] = run(catalog, document())
    assert not again.issuance.cache_hit
    assert caplog.records == []
    [third] = run(catalog, document())
    assert third.issuance.cache_hit
    assert third.issuance.values_from == again.issuance.id


def test_a_row_written_before_the_wording_was_recorded_is_a_quiet_miss(
    world: World, orchard: Orchard, caplog: pytest.LogCaptureFixture
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    [first] = run(catalog, document())
    row = world.store.results.lookup(first.derivation.id, ENGINE, {})
    assert row is not None
    _replaced(world, first.derivation.id, first.issuance.id, _without_wording(row.content))
    with caplog.at_level(logging.ERROR):
        [again] = run(catalog, document())
    assert not again.issuance.cache_hit
    assert caplog.records == []
    [third] = run(catalog, document())
    assert third.issuance.values_from == again.issuance.id

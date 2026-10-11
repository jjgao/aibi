"""``count_cohort``'s answers against the OpenAPI document (SPEC §12.4, D416; ``api.openapi``).

Counts of the orchard, without a disclosure setting and under one (whose suppressed values are
``null``), as their JSON goes over the wire: the builder's schema (MCP's ``outputSchema``) and
the document's ``CohortCounts`` judge each and its perturbations alike (a backstop, beside the
structural tests of ``api/test_openapi.py``), and every number lands on a position marked
``x-aibi-server-number`` (the web client's rule, M5.1c-1).
"""

import json
from collections.abc import Callable
from typing import Any

import pytest

from aibi.core.catalog.service import Catalog
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.engine.worker import Workers
from aibi.core.schema.catalog import TOOL_MODELS
from aibi.core.schema.cohorts import CohortCounts
from aibi.core.schema.export import output_schema
from aibi.core.schema.limits import QueryLimits

TALL = {"kind": "value", "column": "trees.height_m", "range": {"gte": 5}}
HEAVY = {
    "kind": "exists",
    "table": "harvests",
    "where": [{"kind": "value", "column": "harvests.kg", "range": {"gte": 15}}],
}
DOCUMENT = {
    "aibi": "1",
    "dataset": "orchard",
    "unit": "trees",
    "cohorts": {"tall": {"all": [TALL]}, "heavy": {"all": [HEAVY]}, "all": {"all": []}},
}


def nulls(value: Any) -> int:
    if isinstance(value, dict):
        return sum(nulls(member) for member in value.values())
    if isinstance(value, list):
        return sum(nulls(item) for item in value)
    return int(value is None)


@pytest.fixture
def counts(world: Any, orchard: Callable[..., dict[str, bytes]]) -> list[dict[str, Any]]:
    world.publish("orchard", orchard())
    found: list[dict[str, Any]] = []
    for floor in (None, 5):
        catalog = Catalog(world.store, workers=Workers(QueryLimits()), floor=floor)
        answer = call(catalog, BY_NAME["count_cohort"], json.dumps({"document": DOCUMENT}).encode())
        assert isinstance(answer, CohortCounts), answer
        found.append(json.loads(answer.model_dump_json()))
    assert nulls(found[1]) > nulls(found[0])
    return found


def test_the_builder_and_the_document_judge_every_count_alike(
    openapi: Any, counts: list[dict[str, Any]]
) -> None:
    output = TOOL_MODELS["count_cohort"][1]
    builder = output_schema(output, output.__name__)
    found: list[str] = []
    for answer in counts:
        found += openapi.disagreements(builder, output.__name__, answer, 60)
    assert found == []


def test_every_number_of_a_count_lands_on_a_marked_position(
    openapi: Any, counts: list[dict[str, Any]]
) -> None:
    for answer in counts:
        assert openapi.unmarked_numbers(answer, TOOL_MODELS["count_cohort"][1].__name__) == []

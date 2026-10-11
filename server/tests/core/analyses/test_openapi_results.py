"""Result envelopes against the OpenAPI document (SPEC §12.4, D416; ``api.openapi``).

The golden envelopes (``golden/results.json``, run by the reference evaluator over the shop) and
two synthetic ones, as their JSON goes over the wire:

- **the agreement test**, a backstop: each envelope and its perturbations (at every distinct
  generalized position of the golden ones, at spread positions of the synthetic ones) are judged
  alike by the builder's schema (``result.schema.json``, MCP's ``outputSchema``) and the
  document's ``ResultEnvelope``. Its blind spots: a rewrite that changes only what no instance or
  perturbation reaches (a type R2 gives a member no fixture's value matches, a condition moved
  whose branch no fixture takes). The synthetic envelopes reach two of them (a pack's caveat
  code, ``demo.SOMETHING``, which only the pattern member of ``Caveat.code`` takes; an envelope
  over a draft release, perturbed to a cache hit), and the structural tests of
  ``api/test_openapi.py`` (R2's parent type, the never-split comparison) carry the class;
- **the instance oracle**: every number of an envelope lands on a position marked
  ``x-aibi-server-number`` or in a response's ``x-aibi-json`` value (the web client's rule for
  numbers it may not compute with, M5.1c-1).
"""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from aibi.core.schema.export import result_schema

GOLDEN = Path(__file__).parent / "golden" / "results.json"
SYNTHETIC_CAP = 40
"""Positions perturbed in each synthetic envelope; the golden ones are perturbed at every distinct
generalized position (``OpenApi.agreement``)."""


@pytest.fixture(scope="module")
def envelopes(
    analyse: Callable[..., list[Any]], shop: Callable[..., Any]
) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for name, case in json.loads(GOLDEN.read_text(encoding="utf-8")).items():
        [analysed] = analyse(
            case["document"], shop(**case.get("shop", {})), floor=case.get("floor")
        )
        found[name] = json.loads(analysed.result.model_dump_json())
    return found


def pack_caveat(envelope: dict[str, Any]) -> dict[str, Any]:
    """An envelope with a pack's caveat, ``demo.SOMETHING``."""
    changed = copy.deepcopy(envelope)
    changed["caveats"].append(
        {
            "code": "demo.SOMETHING",
            "severity": "warn",
            "message": [{"text": "A pack's caveat"}],
            "affects": ["/values"],
        }
    )
    return changed


def over_a_draft(envelope: dict[str, Any], *, cache_hit: bool) -> dict[str, Any]:
    """An envelope over a draft release, a cache hit or not."""
    changed = copy.deepcopy(envelope)
    for release in changed["derivation"]["releases"]:
        release.update(label="draft", status="draft")
    changed["issuance"]["cache_hit"] = cache_hit
    return changed


def test_the_golden_envelopes_are_the_ones_the_document_describes(
    envelopes: dict[str, dict[str, Any]],
) -> None:
    assert len(envelopes) == 19
    builder = jsonschema.Draft202012Validator(result_schema())
    for name, envelope in envelopes.items():
        assert builder.is_valid(envelope), name


def test_the_builder_and_the_document_judge_every_envelope_alike(
    openapi: Any, envelopes: dict[str, dict[str, Any]]
) -> None:
    """Each envelope, and one change (four kinds) at every distinct generalized position of any:
    a position's shape, a list index being ``[]``, is perturbed once, in the smallest envelope
    that has it."""
    assert openapi.agreement(result_schema(), "ResultEnvelope", envelopes) == []


def test_the_builder_and_the_document_judge_the_synthetic_envelopes_alike(
    openapi: Any, envelopes: dict[str, dict[str, Any]]
) -> None:
    base = envelopes["two_tiers"]
    builder = result_schema()
    validator = jsonschema.Draft202012Validator(builder)
    caveat, draft = pack_caveat(base), over_a_draft(base, cache_hit=False)
    assert validator.is_valid(caveat)
    assert validator.is_valid(draft)
    assert not validator.is_valid(over_a_draft(base, cache_hit=True))
    found: list[str] = []
    for instance in (caveat, draft, over_a_draft(base, cache_hit=True)):
        found += openapi.disagreements(builder, "ResultEnvelope", instance, SYNTHETIC_CAP)
    assert found == []


def test_every_number_of_an_envelope_lands_on_a_marked_position(
    openapi: Any, envelopes: dict[str, dict[str, Any]]
) -> None:
    instances = [*envelopes.values(), pack_caveat(envelopes["two_tiers"])]
    checked = 0
    for envelope in instances:
        assert openapi.unmarked_numbers(envelope, "ResultEnvelope") == []
        checked += json.dumps(envelope).count(":")
    assert checked > 1000


def test_the_validator_cache_is_by_a_schema_s_content_not_its_identity(openapi: Any) -> None:
    """A schema that is a temporary (or a copy) gets its own validator, never the one of an object
    that had its ``id``."""
    first = openapi._of({"anyOf": [{"type": "string"}]})
    assert openapi._of({"anyOf": [{"type": "string"}]}) is first
    other = openapi._of({"anyOf": [{"type": "integer"}]})
    assert other is not first
    assert first.is_valid("a")
    assert not first.is_valid(1)
    assert other.is_valid(1)
    assert not other.is_valid("a")

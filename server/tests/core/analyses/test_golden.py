"""Golden result ids and digests (SPEC §7.6, §9.3, §13.4; D318, D319).

``golden/results.json`` holds documents over the shop, each with the result id, computation id
and digest its view had when it was checked in, computed by the reference evaluator (the SQL
compiler is held to it by ``tests/core/catalog/test_analyses.py``). The same id MUST give the
same digest: a change to either fails here unless a version in the id was bumped (the analysis's
version, a pack's results version or the semantics version), and then the file is written again.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

GOLDEN = Path(__file__).parent / "golden" / "results.json"


def _cases() -> dict[str, dict[str, Any]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(_cases()))
def test_golden_result_ids_and_digests_are_unchanged(
    analyse: Callable[..., list[Any]], shop: Callable[..., Any], name: str
) -> None:
    case = _cases()[name]
    [analysed] = analyse(case["document"], shop(), floor=case.get("floor"))
    found = {
        "id": analysed.result.derivation.id,
        "computation_id": analysed.view.identity.computation_id,
        "digest": analysed.result.digest,
    }
    assert found == {member: case[member] for member in found}


def test_a_floor_changes_the_result_id_but_not_the_computation_id() -> None:
    cases = _cases()
    plain, under_k = cases["two_tiers"], cases["two_tiers_under_k"]
    assert plain["id"] != under_k["id"]
    assert plain["computation_id"] == under_k["computation_id"]
    assert plain["digest"] != under_k["digest"]

"""A release built from raw snapshots, loaded from the store and evaluated by the reference
evaluator (SPEC §12.2, §13.3): what the importers will hand the query engine."""

import json
from typing import Any

from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.resolve import resolve
from aibi.core.schema.loading import load_document
from aibi.core.store.store import Store


def _count(store: Store, manifest: str, clauses: list[Any], unit: str = "members") -> Any:
    written = {"aibi": "1", "dataset": "lib", "unit": unit, "cohorts": {"c": {"all": clauses}}}
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    resolution = resolve(loaded.document, {"lib": store.load(manifest)}, loaded.positions)
    assert resolution.refusals == []
    return evaluate(resolution.cohorts["c"])


def test_a_stored_release_answers_questions(store: Store, imported: str) -> None:
    overdue = {
        "kind": "exists",
        "table": "loans",
        "where": [{"kind": "value", "column": "loans.overdue", "range": {"gt": 0}}],
    }
    result = _count(store, imported, [overdue])
    # m-1's loan is 6 days overdue; m-17's are 11 days early and unknown; the others have none
    # under coverage undeclared, or a loan with no days.
    assert result.values[0].value.value == "TRUE"
    assert (result.n_true, result.n_false + result.n_unknown) == (1, 3)
    older = _count(
        store,
        imported,
        [{"kind": "value", "column": "members.age", "range": {"gte": 30}, "units": "a"}],
    )
    assert [value.value.value for value in older.values] == ["TRUE", "UNKNOWN", "FALSE", "UNKNOWN"]
    interested = _count(
        store, imported, [{"kind": "value", "column": "members.interests", "values": ["maths"]}]
    )
    assert [value.value.value for value in interested.values] == [
        "TRUE",
        "UNKNOWN",
        "TRUE",
        "UNKNOWN",
    ]

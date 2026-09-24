"""The checked-in JSON Schemas are current, valid, and describe real documents."""

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from aibi.core.schema.export import SCHEMAS, render

SCHEMA_DIR = Path(__file__).resolve().parents[4] / "schemas"
DOCUMENTS = Path(__file__).parent / "documents"


def example(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((DOCUMENTS / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_checked_in_schema_is_current(name: str) -> None:
    checked_in = (SCHEMA_DIR / name).read_text(encoding="utf-8")
    assert checked_in == render(SCHEMAS[name]()), (
        f"schemas/{name} is stale: run `uv run python -m aibi.core.schema.export ../schemas`"
    )


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMAS[name]())


def test_schemas_never_allow_null() -> None:
    for build in SCHEMAS.values():
        assert '"null"' not in json.dumps(build())


def test_documents_validate_against_the_as_written_schema() -> None:
    as_written = SCHEMAS["document.as-written.schema.json"]()
    substituted = SCHEMAS["document.schema.json"]()
    for name in ("sites", "trial"):
        jsonschema.validate(example(name), as_written)
    jsonschema.validate(example("trial"), substituted)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(example("sites"), substituted)  # "$min_score" is not a number


def test_a_whole_cohort_may_come_from_a_parameter() -> None:
    document = {
        "aibi": "1",
        "dataset": "d",
        "unit": "t",
        "params": {"c": {"all": []}},
        "cohorts": {"everyone": "$c"},
    }
    jsonschema.validate(document, SCHEMAS["document.as-written.schema.json"]())

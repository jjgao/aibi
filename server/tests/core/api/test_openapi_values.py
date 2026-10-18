"""The values models the OpenAPI document publishes (D434; ``openapi.value_roots``,
``x-aibi-values``), held to the registry as ``list_analyses`` serves it.

For each published analysis and version, the component ``x-aibi-values`` names, with every
reference inlined, equals the entry's ``returns`` as ``list_analyses`` serves it, both stripped, at
keyword positions alone (``inline``), of exactly the annotations the web's generator reads as
annotations (``ANNOTATIONS`` of ``web/scripts/generate-shapes.mjs``: ``title``, ``description``,
``default``, ``x-aibi-computed``, ``x-aibi-data`` and ``x-aibi-server-number``) and of ``$defs``
and ``$schema``; a member's name, a non-schema keyword's value and ``x-aibi-json`` are kept. The
pin is independent of ``value_roots``' own reading of ``registry.CORE``: it compares the published
document with the served listing.
"""

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from tests.core.catalog.world import World, ticking_clock

from aibi.core.api import openapi
from aibi.core.store.store import Store

GENERATOR = Path(__file__).resolve().parents[4] / "web" / "scripts" / "generate-shapes.mjs"
COMPONENTS = "#/components/schemas/"


def annotations() -> tuple[str, ...]:
    """The generator's ``ANNOTATIONS``, read from its source (one list, not a copy)."""
    text = GENERATOR.read_text(encoding="utf-8")
    found = re.search(r"export const ANNOTATIONS = \[([^\]]*)\];", text)
    assert found is not None
    return tuple(json.loads(f"[{found.group(1)}]"))


SCHEMA_MAPS = ("properties", "patternProperties", "$defs", "dependentSchemas")
"""Keywords whose value maps names (of members, patterns, definitions) to schemas."""

SCHEMA_LISTS = ("allOf", "anyOf", "oneOf", "prefixItems")
"""Keywords whose value is a list of schemas."""

SCHEMA_VALUES = (
    "items",
    "additionalProperties",
    "propertyNames",
    "not",
    "if",
    "then",
    "else",
    "contains",
)
"""Keywords whose value is a schema."""


def inline(node: Any, definitions: dict[str, Any], prefix: str, strip: tuple[str, ...]) -> Any:
    """A schema with every reference replaced by its definition, and, at keyword positions alone,
    the annotations ``strip`` names dropped, with ``$defs`` and ``$schema``: a name under
    ``properties``, ``patternProperties``, ``$defs`` or ``dependentSchemas`` is kept whatever it
    is, a keyword's value that is no schema (``enum``, ``const``, ``required``, ``type``, ...) is
    kept whole, and every other keyword, ``x-aibi-json`` among them, is kept."""
    if not isinstance(node, dict):
        return node
    if set(node) == {"$ref"}:
        return inline(definitions[node["$ref"].removeprefix(prefix)], definitions, prefix, strip)
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in strip or key in ("$defs", "$schema"):
            continue
        if key in SCHEMA_MAPS and isinstance(value, dict):
            out[key] = {name: inline(v, definitions, prefix, strip) for name, v in value.items()}
        elif key in SCHEMA_LISTS and isinstance(value, list):
            out[key] = [inline(v, definitions, prefix, strip) for v in value]
        elif key in SCHEMA_VALUES:
            out[key] = inline(value, definitions, prefix, strip)
        else:
            out[key] = value
    return out


def test_inline_strips_annotations_at_keyword_positions_alone() -> None:
    """The strip's own control: a member named like an annotation, an ``x-aibi-json`` mark and a
    ``const`` holding an annotation's name are kept; an annotation keyword at any depth is
    dropped."""
    schema = {
        "title": "T",
        "properties": {
            "title": {"type": "string", "description": "D"},
            "default": {"const": {"title": 1}},
        },
        "items": {"x-aibi-json": {"null": True}, "x-aibi-server-number": True, "default": 3},
    }
    assert inline(schema, {}, "#/", annotations()) == {
        "properties": {"title": {"type": "string"}, "default": {"const": {"title": 1}}},
        "items": {"x-aibi-json": {"null": True}},
    }


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return openapi.document()


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    """``list_analyses``' entries as it serves them, by id."""
    root = tmp_path_factory.mktemp("listing")
    (root / "imports").mkdir()
    world = World(root, Store(root / "data", clock=ticking_clock()))
    try:
        listing = world.tool(world.catalog(), "list_analyses", {})
        assert not isinstance(listing, list)
        body = json.loads(listing.model_dump_json(by_alias=True))
    finally:
        world.store.close()
    return {entry["id"]: entry for entry in body["analyses"]}


def published(document: dict[str, Any], strip: tuple[str, ...]) -> dict[tuple[str, str], Any]:
    """Each published values model, by analysis and version, its references inlined."""
    components = document["components"]["schemas"]
    return {
        (name, version): inline(reference, components, COMPONENTS, strip)
        for name, versions in document[openapi.VALUES_KEY].items()
        for version, reference in versions.items()
    }


def returns(
    served: dict[str, dict[str, Any]], strip: tuple[str, ...]
) -> dict[tuple[str, str], Any]:
    """The served ``returns`` of each analysis the document publishes values for, inlined."""
    found: dict[tuple[str, str], Any] = {}
    for name in openapi.WEB_VALUES:
        entry = served[name]
        model = entry["fields"]["returns"]
        found[(name, entry["version"])] = inline(model, model.get("$defs", {}), "#/$defs/", strip)
    return found


def test_the_annotations_are_the_generator_s() -> None:
    assert annotations() == (
        "title",
        "description",
        "default",
        "x-aibi-computed",
        "x-aibi-data",
        "x-aibi-server-number",
    )


def test_each_published_values_model_is_the_served_returns(
    document: dict[str, Any], served: dict[str, dict[str, Any]]
) -> None:
    strip = annotations()
    found = published(document, strip)
    assert set(found) == {(name, served[name]["version"]) for name in openapi.WEB_VALUES}
    assert found == returns(served, strip)


def test_the_pin_sees_a_model_swapped_and_a_null_arm_retyped_and_not_a_description(
    document: dict[str, Any], served: dict[str, dict[str, Any]]
) -> None:
    """The pin's controls: another component published (killed), a ``null`` arm retyped (killed),
    and a description changed (equivalent: an annotation, stripped)."""
    strip = annotations()
    expected = returns(served, strip)
    [(name, version)] = list(expected)
    swapped = copy.deepcopy(document)
    swapped[openapi.VALUES_KEY][name][version] = {"$ref": f"{COMPONENTS}DistributionPosition"}
    assert published(swapped, strip) != expected
    retyped = copy.deepcopy(document)
    numbers = retyped["components"]["schemas"]["NumberDistribution"]
    text = json.dumps(numbers)
    assert '{"type": "null"}' in text
    retyped["components"]["schemas"]["NumberDistribution"] = json.loads(
        text.replace('{"type": "null"}', '{"type": "string"}', 1)
    )
    assert published(retyped, strip) != expected
    described = copy.deepcopy(document)
    described["components"]["schemas"]["HistogramBin"]["description"] = "another text"
    assert published(described, strip) == expected

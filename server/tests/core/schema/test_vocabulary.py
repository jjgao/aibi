"""The vocabulary partition (SPEC §10.1, §12.4, D422): the 57 keywords of the 2020-12 metaschema's
seven vocabularies, each *rendered* or *refused by the renderer*, read from the metaschema itself,
never from a list typed here; the core's ``params`` use only rendered ones; the generated file is
the constant. That a renderer refuses an unlisted keyword is M5.3's test: it tests the renderer."""

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import jsonschema_specifications
import pytest
from pydantic import JsonValue

from aibi.core.analyses.registry import CORE
from aibi.core.schema import export, jsonschemas, vocabulary
from aibi.core.schema.vocabulary import OUTSIDE, REFUSED, RENDERED, VOCABULARIES

ROOT = Path(__file__).resolve().parents[4]
SCHEMA_DIR = ROOT / "schemas"
METASCHEMA = "https://json-schema.org/draft/2020-12/schema"
ANNOTATIONS = {"title", "description", "default", "examples", "readOnly", "deprecated", "$comment"}
STRAY = {"ge"}
"""The keywords the core's ``params`` hold that the dialect does not have: Pydantic writes the
constraint ``Field(ge=0)`` of ``Time`` (``Annotated[Finite, ...]``) as ``ge`` where it should write
``minimum`` (``survival.km``, ``landmarks`` and ``grid``). A renderer refuses them by the rule
(D422); fixing it changes the analysis's output and so its version. This pin fails when it is
fixed, and when another appears."""


def _metaschema() -> dict[str, Any]:
    return cast(dict[str, Any], jsonschema_specifications.REGISTRY.contents(METASCHEMA))


def _vocabularies() -> dict[str, dict[str, Any]]:
    """Each vocabulary of the metaschema, by its name, as the metaschema's ``allOf`` lists them."""
    found: dict[str, dict[str, Any]] = {}
    for member in _metaschema()["allOf"]:
        name = member["$ref"].removeprefix("meta/")
        found[name] = cast(
            dict[str, Any],
            jsonschema_specifications.REGISTRY.contents(vocabulary.VOCABULARY_BASE + name),
        )
    return found


def _keywords() -> dict[str, set[str]]:
    return {name: set(body["properties"]) for name, body in _vocabularies().items()}


# --- The partition covers the metaschema's vocabulary exactly -----------------------------------


def test_the_metaschema_has_seven_vocabularies_and_the_partition_the_same() -> None:
    assert len(_keywords()) == 7
    assert set(VOCABULARIES) == set(_keywords())
    assert sum(len(found) for found in _keywords().values()) == 57
    for name, body in _vocabularies().items():
        assert body["$id"] == vocabulary.VOCABULARY_BASE + name


@pytest.mark.parametrize("name", sorted(_keywords()))
def test_each_vocabulary_is_covered_exactly_by_its_two_classes(name: str) -> None:
    held = VOCABULARIES[name]
    assert set(held.rendered) | set(held.refused) == _keywords()[name], "missing or extra"
    assert not set(held.rendered) & set(held.refused), "in both classes"
    assert len(held.rendered) == len(set(held.rendered))
    assert len(held.refused) == len(set(held.refused))
    assert held.keywords == _keywords()[name]


def test_every_keyword_is_in_exactly_one_class_of_exactly_one_vocabulary() -> None:
    every = set().union(*_keywords().values())
    assert len(every) == 57
    assert every == RENDERED | REFUSED
    assert not RENDERED & REFUSED
    listed = [k for held in VOCABULARIES.values() for k in (*held.rendered, *held.refused)]
    assert len(listed) == len(set(listed)) == 57
    assert len(RENDERED) + len(REFUSED) == 57


def test_the_metaschema_s_other_top_level_keywords_are_outside_and_refused() -> None:
    top = set(_metaschema()["properties"])
    assert (
        top == set(OUTSIDE) == {"definitions", "dependencies", "$recursiveAnchor", "$recursiveRef"}
    )
    assert not top & (RENDERED | REFUSED)
    assert all(vocabulary.refuses(keyword) for keyword in OUTSIDE)


def test_a_keyword_in_no_list_is_refused_and_a_rendered_one_is_not() -> None:
    assert vocabulary.UNLISTED == "refused"
    for keyword in ("x-widget", "foo", "ge", "", "Type", "$ID"):
        assert vocabulary.refuses(keyword)
    for keyword in REFUSED:
        assert vocabulary.refuses(keyword)
    for keyword in RENDERED:
        assert not vocabulary.refuses(keyword)


def test_the_annotations_a_form_needs_are_in_the_partition_and_rendered() -> None:
    assert set().union(*_keywords().values()) >= ANNOTATIONS
    assert ANNOTATIONS <= RENDERED


def test_the_validators_of_the_dialect_are_inside_the_vocabulary() -> None:
    """``jsonschemas._KEYWORDS`` is the validation keywords the core's validator evaluates:
    all of them are among the 57, and 21 of the 57 are not (the annotations and references)."""
    evaluated = set(jsonschemas._KEYWORDS)  # pyright: ignore[reportPrivateUsage]
    assert evaluated <= RENDERED | REFUSED
    assert len(RENDERED | REFUSED) - len(evaluated & (RENDERED | REFUSED)) == 21


def test_a_pack_s_schema_may_not_hold_pattern_so_rendered_is_stated_for_the_core() -> None:
    """D247: ``pattern`` and ``patternProperties`` are rendered for the core's own ``params``;
    registration refuses them in a pack's schema; and admits keywords the dialect does not know,
    which the renderer then refuses (its test is M5.3's)."""
    assert {"pattern", "patternProperties"} <= RENDERED
    refused: list[dict[str, JsonValue]] = [
        {"type": "object", "pattern": "^a$"},
        {"type": "object", "patternProperties": {"^a": {"type": "string"}}},
    ]
    for schema in refused:
        assert jsonschemas.problems(schema), schema
    for unknown in ("x-widget", "foo", "ge"):
        assert jsonschemas.problems({"type": "object", unknown: "x"}) == [], unknown
        assert vocabulary.refuses(unknown)


# --- What the core's params use is rendered -----------------------------------------------------

_MAPS = {"properties", "patternProperties", "$defs", "dependentSchemas"}
_LISTS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_ONE = {
    "items",
    "additionalProperties",
    "contains",
    "not",
    "if",
    "then",
    "else",
    "propertyNames",
    "unevaluatedItems",
    "unevaluatedProperties",
    "contentSchema",
}


def keywords_used(schema: JsonValue) -> set[str]:
    """The keywords that stand as keywords in ``schema``: a walk that tells keyword positions
    from data (the names under ``properties``, the values of ``default``, ``enum``, ``const``,
    ``examples`` and ``required`` are not keywords)."""
    found: set[str] = set()
    if not isinstance(schema, dict):
        return found
    for keyword, value in schema.items():
        found.add(keyword)
        if keyword in _MAPS and isinstance(value, dict):
            for member in value.values():
                found |= keywords_used(member)
        elif keyword in _LISTS and isinstance(value, list):
            for member in value:
                found |= keywords_used(member)
        elif keyword in _ONE:
            found |= keywords_used(value)
    return found


POSITIONS_MAP = ("properties", "patternProperties", "$defs", "dependentSchemas")
POSITIONS_LIST = ("allOf", "anyOf", "oneOf", "prefixItems")
POSITIONS_ONE = (
    "items",
    "additionalProperties",
    "contains",
    "not",
    "if",
    "then",
    "else",
    "propertyNames",
    "unevaluatedItems",
    "unevaluatedProperties",
    "contentSchema",
)
"""The positions where a subschema stands, written out here and not read from the walk."""


@pytest.mark.parametrize("position", [*POSITIONS_MAP, *POSITIONS_LIST, *POSITIONS_ONE])
def test_the_walk_descends_into_each_position_where_a_subschema_stands(position: str) -> None:
    child: JsonValue = {f"x-under-{position}": 1}
    if position in POSITIONS_MAP:
        schema: JsonValue = {position: {"a name": child}}
        expected = {position, f"x-under-{position}"}
    elif position in POSITIONS_LIST:
        schema = {position: [{"type": "string"}, child]}
        expected = {position, "type", f"x-under-{position}"}
    else:
        schema = {position: child}
        expected = {position, f"x-under-{position}"}
    assert keywords_used(schema) == expected


def test_the_walk_descends_through_positions_inside_one_another() -> None:
    schema: JsonValue = {
        "allOf": [{"anyOf": [{"properties": {"n": {"not": {"items": {"x-deep": 1}}}}}]}]
    }
    assert keywords_used(schema) == {"allOf", "anyOf", "properties", "not", "items", "x-deep"}


def test_the_walk_does_not_descend_into_data() -> None:
    """A keyword found nowhere else sits in each data position: none is counted; a property
    named like a keyword is a name, not a keyword."""
    schema: JsonValue = {
        "default": {"x-d-default": 1},
        "examples": [{"x-d-examples": 1}, [{"x-d-nested": 1}]],
        "const": {"x-d-const": 1},
        "enum": [{"x-d-enum": 1}],
        "required": [
            "x-d-required",
            {"x-d-req": 1},
        ],  # no schema, but a walk that descended finds it
        "dependentRequired": {"x-d-key": ["x-d-value"]},
        "properties": {"contains": {}, "x-name": {"type": "string"}},
        "patternProperties": {"x-pattern": {"minimum": 1}},
        "$defs": {"x-def": {"maximum": 2}},
        "dependentSchemas": {"x-dep": {"minLength": 1}},
    }
    assert keywords_used(schema) == {
        "default",
        "examples",
        "const",
        "enum",
        "required",
        "dependentRequired",
        "properties",
        "type",
        "patternProperties",
        "minimum",
        "$defs",
        "maximum",
        "dependentSchemas",
        "minLength",
    }
    assert "contains" not in keywords_used(schema)
    assert not any(keyword.startswith("x-") for keyword in keywords_used(schema))


def test_every_keyword_of_the_core_s_params_is_rendered() -> None:
    used: set[str] = set()
    for analysis in CORE.values():
        found = keywords_used(analysis.entry.fields.params)
        assert found, analysis.entry.id
        used |= found
    assert {
        "pattern",
        "patternProperties",
        "propertyNames",
        "if",
        "then",
        "dependentRequired",
        "$schema",
        "$ref",
        "$defs",
        "oneOf",
        "anyOf",
        "not",
    } <= used, "the walk finds what the core uses"
    inside = used & (RENDERED | REFUSED)
    assert inside <= RENDERED, f"refused by the renderer: {sorted(inside - RENDERED)}"
    assert used - (RENDERED | REFUSED) == STRAY, "outside the vocabulary"


def test_the_stray_keyword_is_in_survival_km_alone() -> None:
    where = {
        analysis.entry.id
        for analysis in CORE.values()
        if STRAY & keywords_used(analysis.entry.fields.params)
    }
    assert where == {"survival.km"}


# --- The classification itself is a decision: a golden, so that moving a keyword is an edit ------

REFUSED_GOLDEN = frozenset(
    {
        "$anchor",
        "$dynamicAnchor",
        "$dynamicRef",
        "$vocabulary",
        "contains",
        "contentEncoding",
        "contentMediaType",
        "contentSchema",
        "dependentSchemas",
        "maxContains",
        "minContains",
        "prefixItems",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)
"""The 14 keywords D422 names as refused, written out: moving one is an edit of this list, of
`vocabulary.py`, of D422 and of the generated file together."""
RENDERED_GOLDEN = frozenset(
    {
        "$comment",
        "$defs",
        "$id",
        "$ref",
        "$schema",
        "additionalProperties",
        "allOf",
        "anyOf",
        "const",
        "default",
        "dependentRequired",
        "deprecated",
        "description",
        "else",
        "enum",
        "examples",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "if",
        "items",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "not",
        "oneOf",
        "pattern",
        "patternProperties",
        "properties",
        "propertyNames",
        "readOnly",
        "required",
        "then",
        "title",
        "type",
        "uniqueItems",
        "writeOnly",
    }
)
"""The 43 rendered ones."""
UNUSED = frozenset(
    {
        "$comment",
        "$id",
        "allOf",
        "deprecated",
        "else",
        "examples",
        "format",
        "minLength",
        "multipleOf",
        "readOnly",
        "uniqueItems",
        "writeOnly",
    }
)
"""The rendered keywords no core analysis's ``params`` uses (D422 says so)."""


def test_the_classification_is_the_golden() -> None:
    assert REFUSED == REFUSED_GOLDEN
    assert RENDERED == RENDERED_GOLDEN
    assert len(REFUSED_GOLDEN) == 14
    assert len(RENDERED_GOLDEN) == 43
    assert not REFUSED_GOLDEN & RENDERED_GOLDEN
    found = _exported()
    assert found["refused"] == sorted(REFUSED_GOLDEN)
    assert found["rendered"] == sorted(RENDERED_GOLDEN)


def test_what_d422_names_as_refused_is_refused() -> None:
    text = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    [row] = [line for line in text.splitlines() if line.startswith("| D422 |")]
    named = re.search(r"among them (.*?)\)", row)
    assert named, "D422 names the refused keywords it gives as examples"
    keywords = set(re.findall(r"`([^`]+)`", named.group(1)))
    assert keywords >= {"$anchor", "$dynamicRef", "contains", "prefixItems", "dependentSchemas"}
    for keyword in keywords:
        assert keyword in REFUSED or keyword.startswith("unevaluated"), keyword
    assert {"unevaluatedItems", "unevaluatedProperties"} <= REFUSED


def test_the_rendered_keywords_no_core_params_uses_are_the_twelve_d422_names() -> None:
    used: set[str] = set()
    for analysis in CORE.values():
        used |= keywords_used(analysis.entry.fields.params)
    assert RENDERED - used == UNUSED
    assert len(UNUSED) == 12
    assert "12 rendered keywords" in (ROOT / "SPEC.md").read_text(encoding="utf-8")


# --- D422's numbers and lists, rendered from the code and asserted present ---------------------

CORE_USES = ("pattern", "patternProperties", "propertyNames", "if", "then", "dependentRequired")
"""The keywords D422 names, with ``$schema``, as ones the core's ``params`` use."""
ANNOTATION_ORDER = (
    "title",
    "description",
    "default",
    "examples",
    "readOnly",
    "deprecated",
    "$comment",
)
REFUSED_EXAMPLES = ("$anchor", "$dynamicRef", "contains", "prefixItems", "dependentSchemas")
NUMBERS = {2: "two", 7: "seven", 12: "12"}


def listed(items: Sequence[str]) -> str:
    """``a``, ``b`` and ``c``, each in backticks."""
    ticked = [f"`{item}`" for item in items]
    return f"{', '.join(ticked[:-1])} and {ticked[-1]}"


def stray_analyses() -> list[str]:
    return sorted(
        analysis.entry.id
        for analysis in CORE.values()
        if STRAY & keywords_used(analysis.entry.fields.params)
    )


def ticked(items: Sequence[str]) -> str:
    return ", ".join(f"`{item}`" for item in items)


def vocabulary_sentences() -> list[str]:
    per = ", ".join(f"{name} {len(held.keywords)}" for name, held in VOCABULARIES.items())
    outside = sorted(OUTSIDE, key=lambda keyword: (keyword.startswith("$"), keyword))
    [where] = stray_analyses()
    [stray] = STRAY
    every = len(RENDERED | REFUSED)
    return [
        f"partitions the {every} keywords of the {NUMBERS[len(VOCABULARIES)]} vocabularies of "
        f"the 2020-12 metaschema ({per}) into *rendered* (a form renderer can draw a form that "
        f"agrees with the server about it: {len(RENDERED)}) and *refused* (a renderer refuses a "
        f"schema that holds one: {len(REFUSED)}, among them {ticked(REFUSED_EXAMPLES)}, "
        "the two `unevaluated` keywords and the content keywords)",
        f"a test holds the exact {len(REFUSED)} and {len(RENDERED)} as a golden",
        f"The metaschema's top level also names {listed(outside)}, outside the {every}: refused.",
        f"the annotations a form needs ({ticked(ANNOTATION_ORDER)}) are "
        "shown, never applied, and so is `format`",
        f"{len(UNUSED)} rendered keywords are used by no core `params` ({ticked(sorted(UNUSED))})",
        f"{ticked(CORE_USES)} and `$schema` among them",
        f"**A known defect, not fixed here:** `{where}`'s `params` hold `{stray}`",
        f"a test pins `{stray}` as the only keyword of the core's `params` outside the vocabulary",
        f"M5.3 renders every one of the {len(RENDERED)} or moves the ones it does not render to "
        "*refused*",
    ]


def test_d422_holds_every_number_and_list_of_its_vocabulary_paragraph() -> None:
    row = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    [row] = [line for line in row.splitlines() if line.startswith("| D422 |")]
    for sentence in vocabulary_sentences():
        assert sentence in row, sentence


def test_what_the_sentences_name_is_what_the_code_has() -> None:
    assert set(ANNOTATION_ORDER) == ANNOTATIONS
    assert set(REFUSED_EXAMPLES) <= REFUSED
    used: set[str] = set()
    for analysis in CORE.values():
        used |= keywords_used(analysis.entry.fields.params)
    assert {*CORE_USES, "$schema"} <= used
    assert stray_analyses() == ["survival.km"]
    assert set(CORE_USES) <= RENDERED


# --- The exported file is the constant ---------------------------------------------------------


def _exported() -> dict[str, Any]:
    found: dict[str, Any] = json.loads(
        (SCHEMA_DIR / vocabulary.VOCABULARY_FILE).read_text(encoding="utf-8")
    )
    return found


def test_the_checked_in_file_is_current() -> None:
    assert (SCHEMA_DIR / vocabulary.VOCABULARY_FILE).read_text(encoding="utf-8") == export.render(
        vocabulary.document()
    ), "schemas/vocabulary.json is stale: run `uv run python -m aibi.core.schema.export ../schemas`"


def test_the_export_command_writes_the_file(tmp_path: Path) -> None:
    export.write(tmp_path)
    assert (tmp_path / vocabulary.VOCABULARY_FILE).read_text(encoding="utf-8") == (
        SCHEMA_DIR / vocabulary.VOCABULARY_FILE
    ).read_text(encoding="utf-8")


def test_the_file_holds_exactly_the_constant_and_the_metaschema_s_keywords() -> None:
    """Read back, not rebuilt by ``document()``: what M5.3 generates its refusal list from."""
    found = _exported()
    assert set(found) == {
        "dialect",
        "rule",
        "unlisted",
        "vocabularies",
        "rendered",
        "refused",
        "outside",
    }
    assert found["dialect"] == METASCHEMA
    assert found["unlisted"] == "refused"
    assert found["outside"] == sorted(set(_metaschema()["properties"]))
    every = set().union(*_keywords().values())
    assert set(found["rendered"]) | set(found["refused"]) == every
    assert not set(found["rendered"]) & set(found["refused"])
    assert found["rendered"] == sorted(RENDERED)
    assert found["refused"] == sorted(REFUSED)
    assert set(found["vocabularies"]) == set(_keywords())
    for name, held in found["vocabularies"].items():
        assert held["id"] == vocabulary.VOCABULARY_BASE + name
        assert set(held["rendered"]) | set(held["refused"]) == _keywords()[name]
        assert not set(held["rendered"]) & set(held["refused"])
        assert set(held["rendered"]) <= set(found["rendered"])
        assert set(held["refused"]) <= set(found["refused"])
    assert set(found["rendered"]) >= ANNOTATIONS


def test_schemas_names_the_vocabulary_among_what_the_command_writes() -> None:
    written = {path.name for path in SCHEMA_DIR.iterdir()}
    assert vocabulary.VOCABULARY_FILE in written
    assert vocabulary.VOCABULARY_FILE not in export.SCHEMAS, "it is no schema"

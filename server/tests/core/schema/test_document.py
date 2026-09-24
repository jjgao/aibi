"""Documents as written: shape, substitution, whole-document checks and refusal paths."""

import copy
import gc
import json
import math
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from aibi.core.schema import document as document_module
from aibi.core.schema.document import (
    AllClause,
    Document,
    ExistsLeaf,
    NotClause,
    PackLeaf,
    UnknownClause,
    ValueLeaf,
)
from aibi.core.schema.export import SCHEMAS
from aibi.core.schema.jsonio import escape_token
from aibi.core.schema.limits import (
    MAX_COHORTS,
    MAX_COLUMNS,
    MAX_DATASETS,
    MAX_DEPTH,
    MAX_DOCUMENT_BYTES,
    MAX_LIST,
    MAX_PACKS,
    MAX_PARAMS,
    MAX_POINTER,
    MAX_POINTERS,
    MAX_REFUSALS,
    MAX_VALUES,
)
from aibi.core.schema.loading import (
    DocumentResult,
    _finish,  # pyright: ignore[reportPrivateUsage]
    _Found,  # pyright: ignore[reportPrivateUsage]
    _kept,  # pyright: ignore[reportPrivateUsage]
    _Positions,  # pyright: ignore[reportPrivateUsage]
    load_document,
    refusal_from_error,
)
from aibi.core.schema.params import NEAREST, nearest
from aibi.core.schema.refusals import Refusal, RefusalCode

DOCUMENTS = Path(__file__).parent / "documents"


def example(name: str) -> dict[str, Any]:
    """An example document: ``sites`` uses parameters, ``library`` uses a test pack's leaf."""
    loaded: dict[str, Any] = json.loads((DOCUMENTS / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


SITES = example("sites")
LIBRARY = example("library")


def load(document: dict[str, Any]) -> DocumentResult:
    return load_document(json.dumps(document))


def refusals(document: dict[str, Any]) -> list[tuple[str, str | None]]:
    return [(refusal.code, refusal.path) for refusal in load(document).refusals]


def with_clause(clause: Any) -> dict[str, Any]:
    return {"aibi": "1", "dataset": "d", "unit": "t", "cohorts": {"c": {"all": [clause]}}}


def test_sites_document_loads_with_parameters() -> None:
    result = load(SITES)
    assert result.refusals == []
    assert result.document is not None
    assert result.params_used == {"min_score": 80, "regions": ["north", "east"]}
    assert result.params_unused == []
    exists = result.document.cohorts["failed"].all[1]
    assert isinstance(exists, ExistsLeaf)
    assert exists.where is not None
    first, second = exists.where
    assert isinstance(first, ValueLeaf)
    assert first.range is not None
    assert first.range.lt == 80
    assert isinstance(second, NotClause)
    assert isinstance(second.not_, ValueLeaf)
    assert second.not_.values == ["north", "east"]


def test_library_document_loads() -> None:
    result = load(LIBRARY)
    assert result.refusals == []
    assert result.document is not None
    clauses = result.document.cohorts["late_readers"].all
    assert isinstance(clauses[0], ValueLeaf)
    assert isinstance(clauses[3], AllClause) is False
    unknown = clauses[2]
    assert unknown.model_dump(by_alias=True) == {
        "unknown": {"kind": "testpack.flag", "q": "fines: UNPAID"}
    }
    assert isinstance(unknown, UnknownClause)
    assert isinstance(unknown.unknown, PackLeaf)


def test_unused_parameters_are_reported() -> None:
    document = copy.deepcopy(SITES)
    document["params"]["spare"] = True
    assert load(document).params_unused == ["spare"]


def test_empty_cohort_selects_every_row() -> None:
    assert load(
        {"aibi": "1", "dataset": "d", "unit": "t", "cohorts": {"all_rows": {"all": []}}}
    ).document


@pytest.mark.parametrize(
    ("clause", "expected"),
    [
        ({"kind": "value", "column": "t.c"}, [("CONFLICTING_MEMBERS", "/cohorts/c/all/0")]),
        (
            {"kind": "value", "column": "t.c", "values": [1], "range": {"gt": 1}},
            [("CONFLICTING_MEMBERS", "/cohorts/c/all/0")],
        ),
        (
            {"kind": "value", "column": "t.c", "op": "="},
            [("CONFLICTING_MEMBERS", "/cohorts/c/all/0")],
        ),
        (
            {"kind": "value", "column": "t.c", "range": {}},
            [("INVALID_VALUE", "/cohorts/c/all/0/range")],
        ),
        (
            {"kind": "value", "column": "t.c", "range": {"gt": 1, "gte": 2}},
            [("CONFLICTING_MEMBERS", "/cohorts/c/all/0/range")],
        ),
        (
            {"kind": "value", "column": "t.c", "range": {"lt": True}},
            [("WRONG_TYPE", "/cohorts/c/all/0/range/lt")],
        ),
        (
            {"kind": "value", "column": "t.c", "values": []},
            [("INVALID_VALUE", "/cohorts/c/all/0/values")],
        ),
        (
            {"kind": "value", "column": "t.c", "values": [[1]]},
            [("WRONG_TYPE", "/cohorts/c/all/0/values/0")],
        ),
        (
            {"kind": "value", "column": "c", "values": [1]},
            [("INVALID_VALUE", "/cohorts/c/all/0/column")],
        ),
        (
            {"kind": "value", "column": "t.c", "values": [1], "negate": "yes"},
            [("WRONG_TYPE", "/cohorts/c/all/0/negate")],
        ),
        (
            {"kind": "value", "column": "t.c", "values": [1], "lift": "loose"},
            [("INVALID_VALUE", "/cohorts/c/all/0/lift")],
        ),
        (
            {"kind": "value", "column": "t.c", "values": [1], "quantifier": []},
            [("INVALID_VALUE", "/cohorts/c/all/0/quantifier")],
        ),
        (
            {"kind": "value", "column": "t.c", "values": [1], "quantifier": {"some": 0}},
            [("INVALID_VALUE", "/cohorts/c/all/0/quantifier/some")],
        ),
        (
            {
                "kind": "value",
                "column": "t.c",
                "values": [1],
                "via": [{"rel": "loans", "dir": "down"}],
            },
            [("INVALID_VALUE", "/cohorts/c/all/0/via/0/rel")],
        ),
        (
            {
                "kind": "value",
                "column": "t.c",
                "values": [1],
                "via": {"d": [{"rel": "rel:a.b", "dir": "sideways"}]},
            },
            [("INVALID_VALUE", "/cohorts/c/all/0/via/d/0/dir")],
        ),
        (
            {"kind": "exists", "table": "s", "quantifier": "every", "min_count": 2},
            [("CONFLICTING_MEMBERS", "/cohorts/c/all/0")],
        ),
        (
            {"kind": "exists", "table": "s", "quantifier": {"some": 3}, "min_count": 2},
            [("CONFLICTING_MEMBERS", "/cohorts/c/all/0")],
        ),
        (
            {"kind": "covered", "table": "m", "scope": {}},
            [("INVALID_VALUE", "/cohorts/c/all/0/scope")],
        ),
        ({"kind": "ids", "ids": ["no-dataset"]}, [("INVALID_VALUE", "/cohorts/c/all/0/ids/0")]),
        (
            {"kind": "ids", "ids": [{"dataset": "d", "key": []}]},
            [("INVALID_VALUE", "/cohorts/c/all/0/ids/0/key")],
        ),
        ({"kind": "cohort", "cohort": "1bad"}, [("INVALID_VALUE", "/cohorts/c/all/0/cohort")]),
        ({"kind": "valeu", "column": "t.c"}, [("UNKNOWN_KIND", "/cohorts/c/all/0")]),
        ({"kind": 3}, [("UNKNOWN_KIND", "/cohorts/c/all/0")]),
        ({"kind": "survival.km"}, [("INVALID_VALUE", "/cohorts/c/all/0")]),
        ({"all": [], "any": []}, [("WRONG_TYPE", "/cohorts/c/all/0")]),
        ("overdue", [("WRONG_TYPE", "/cohorts/c/all/0")]),
        (
            {"not": {"kind": "value", "column": "t.c", "values": [1]}, "extra": 1},
            [("UNKNOWN_MEMBER", "/cohorts/c/all/0/extra")],
        ),
        (
            {"kind": "value", "column": "t.c", "values": ["$$x\n", "d:a\rb"], "quantifier": 5},
            [("WRONG_TYPE", "/cohorts/c/all/0/quantifier")],
        ),
        ({"kind": "testpack.x\n"}, [("UNKNOWN_KIND", "/cohorts/c/all/0")]),
        ({"kind": "ids", "ids": ["d:a\rb"]}, [("INVALID_VALUE", "/cohorts/c/all/0/ids/0")]),
        (
            {"not": {"kind": "value", "column": "t.c", "values": [1], "extra": 1}},
            [("UNKNOWN_MEMBER", "/cohorts/c/all/0/not/extra")],
        ),
        (
            {"kind": "value", "column": "t.c", "values": [2**53]},
            [("INTEGER_OUT_OF_RANGE", "/cohorts/c/all/0/values/0")],
        ),
    ],
)
def test_clause_refusals(clause: Any, expected: list[tuple[str, str]]) -> None:
    assert refusals(with_clause(clause)) == expected


def test_unknown_member_lists_the_members_it_could_be() -> None:
    [refusal] = load(with_clause({"kind": "exists", "table": "s", "wher": []})).refusals
    assert refusal.code == "UNKNOWN_MEMBER"
    assert refusal.message[1].model_dump() == {"data": "wher"}
    assert [segment.model_dump()["text"] for segment in refusal.alternatives] == [
        "exclude_self",
        "kind",
        "lift",
        "min_count",
        "quantifier",
        "table",
        "via",
        "where",
    ]


def test_literal_refusals_list_the_allowed_values() -> None:
    [refusal] = load(
        with_clause({"kind": "value", "column": "t.c", "values": [1], "match": "some"})
    ).refusals
    assert [segment.model_dump()["text"] for segment in refusal.alternatives] == ['"any"', '"all"']


def test_pack_leaves_keep_their_members_for_the_pack_to_check() -> None:
    result = load(
        with_clause({"kind": "testpack.flag", "q": "fines: UNPAID", "anything": [1, {"x": True}]})
    )
    assert result.document is not None
    leaf = result.document.cohorts["c"].all[0]
    assert isinstance(leaf, PackLeaf)
    assert leaf.model_extra == {"q": "fines: UNPAID", "anything": [1, {"x": True}]}


def test_nulls_are_refused_everywhere() -> None:
    document = copy.deepcopy(SITES)
    document["notes"] = None
    document["params"]["min_score"] = None
    assert refusals(document) == [
        ("NULL_NOT_ALLOWED", "/notes"),
        ("NULL_NOT_ALLOWED", "/params/min_score"),
    ]


def test_parse_problems_are_refusals() -> None:
    assert [(r.code, r.path) for r in load_document('{"aibi": "1", "aibi": "1"}').refusals] == [
        ("DUPLICATE_KEY", "/aibi")
    ]
    assert [(r.code, r.path) for r in load_document("[]").refusals] == [("WRONG_TYPE", "")]
    [too_large] = load_document(" " * (MAX_DOCUMENT_BYTES + 1)).refusals
    assert too_large.code == "LIMIT_EXCEEDED"
    assert too_large.limit is not None
    assert too_large.limit.max == MAX_DOCUMENT_BYTES


def test_lists_are_capped() -> None:
    [refusal] = load(
        with_clause({"kind": "value", "column": "t.c", "values": list(range(MAX_LIST + 1))})
    ).refusals
    assert (refusal.code, refusal.path) == ("LIMIT_EXCEEDED", "/cohorts/c/all/0/values")
    assert refusal.limit is not None
    assert refusal.limit.max == MAX_LIST


def test_document_level_members() -> None:
    base: dict[str, Any] = {"aibi": "1", "dataset": "d", "unit": "t", "cohorts": {"c": {"all": []}}}
    assert refusals({**base, "aibi": "2"}) == [("INVALID_VALUE", "/aibi")]
    assert refusals({**base, "cohorts": {}}) == [("INVALID_VALUE", "/cohorts")]
    assert refusals({**base, "cohorts": {f"c{i}": {"all": []} for i in range(7)}}) == [
        ("LIMIT_EXCEEDED", "/cohorts")
    ]
    assert refusals({**base, "cohorts": {"bad-name": {"all": []}}}) == [
        ("INVALID_VALUE", "/cohorts/bad-name")
    ]
    assert refusals({**base, "packs": {"testpack": "not a specifier"}}) == [
        ("INVALID_VALUE", "/packs/testpack")
    ]
    assert refusals({**base, "cohorts": {"leaf:value": {"all": []}}}) == [
        ("INVALID_VALUE", "/cohorts/leaf:value")
    ]
    assert refusals({**base, "packs": {"core": ">=1"}}) == [("INVALID_VALUE", "/packs/core")]
    assert refusals({**base, "drafted_by": "someone"}) == [("INVALID_VALUE", "/drafted_by")]
    assert refusals({**base, "dataset": "d@latest"}) == [("INVALID_VALUE", "/dataset")]
    assert refusals({k: v for k, v in base.items() if k != "unit"}) == [("MISSING_MEMBER", "/unit")]


def test_cohort_datasets() -> None:
    base: dict[str, Any] = {"aibi": "1", "unit": "core:person", "cohorts": {}}
    assert refusals({**base, "cohorts": {"c": {"all": []}}}) == [("DATASET_MISSING", "/cohorts/c")]
    assert refusals(
        {**base, "cohorts": {"c": {"all": [], "dataset": "a", "datasets": ["a", "b"]}}}
    ) == [("CONFLICTING_MEMBERS", "/cohorts/c")]
    assert refusals({**base, "cohorts": {"c": {"all": [], "datasets": ["a@1", "a@2"]}}}) == [
        ("DUPLICATE_ENTRY", "/cohorts/c/datasets/1")
    ]
    assert load({**base, "cohorts": {"c": {"all": [], "datasets": ["a", "b@2"]}}}).document


def test_cohort_references() -> None:
    document: dict[str, Any] = {
        "aibi": "1",
        "dataset": "d",
        "unit": "t",
        "cohorts": {
            "a": {"all": [{"kind": "cohort", "cohort": "b"}]},
            "b": {"all": [{"any": [{"kind": "cohort", "cohort": "a"}]}]},
            "c": {"all": [{"kind": "cohort", "cohort": "missing"}]},
            "d": {"all": [{"kind": "cohort", "cohort": "a"}], "dataset": "other"},
            "e": {
                "all": [
                    {
                        "kind": "exists",
                        "table": "s",
                        "where": [
                            {"kind": "ids", "ids": ["d:1"]},
                            {"not": {"kind": "cohort", "cohort": "a"}},
                        ],
                    }
                ]
            },
        },
    }
    assert refusals(document) == [
        ("COHORT_CYCLE", "/cohorts/a"),
        ("COHORT_CYCLE", "/cohorts/b"),
        ("UNKNOWN_COHORT", "/cohorts/c/all/0/cohort"),
        ("COHORT_MISMATCH", "/cohorts/d/all/0/cohort"),
        ("LEAF_NOT_ALLOWED", "/cohorts/e/all/0/where/0"),
        ("LEAF_NOT_ALLOWED", "/cohorts/e/all/0/where/1/not"),
    ]


def test_views() -> None:
    base: dict[str, Any] = {
        "aibi": "1",
        "dataset": "d",
        "unit": "t",
        "cohorts": {"a": {"all": []}, "b": {"all": []}},
    }
    assert refusals(
        {**base, "views": [{"analysis": "compare.columns", "cohorts": ["a", "x"]}]}
    ) == [("UNKNOWN_COHORT", "/views/0/cohorts/1")]
    assert refusals({**base, "views": [{"analysis": "compare.columns", "reference": "x"}]}) == [
        ("UNKNOWN_COHORT", "/views/0/reference")
    ]
    assert refusals(
        {**base, "views": [{"analysis": "compare.columns", "cohorts": ["a"], "reference": "b"}]}
    ) == [("REFERENCE_NOT_IN_VIEW", "/views/0/reference")]
    assert refusals(
        {**base, "views": [{"analysis": "compare.columns", "cohorts": ["a", "a"]}]}
    ) == [("DUPLICATE_ENTRY", "/views/0/cohorts/1")]
    assert refusals({**base, "views": [{"analysis": "km"}]}) == [
        ("INVALID_VALUE", "/views/0/analysis")
    ]
    assert load(
        {**base, "views": [{"analysis": "survival.km", "params": {"landmarks": [12, 24]}}]}
    ).document


def test_problems_inside_parameter_values_point_at_the_reference() -> None:
    document = with_clause({"kind": "value", "column": "t.c", "values": "$v"})
    document["params"] = {"v": [1, {"x": 2}]}
    [refusal] = load(document).refusals
    assert (refusal.code, refusal.path) == ("WRONG_TYPE", "/cohorts/c/all/0/values")
    assert {"data": "v"} in [segment.model_dump() for segment in refusal.message]


def test_refusals_are_sorted_by_path_then_code() -> None:
    document = with_clause({"kind": "value", "column": "bad", "values": [], "extra": 1})
    paths = [path for _, path in refusals(document)]
    assert paths == sorted(paths, key=lambda path: path or "")


def test_cohort_references_compare_datasets_without_pins() -> None:
    document: dict[str, Any] = {
        "aibi": "1",
        "dataset": "d",
        "unit": "t",
        "cohorts": {
            "base": {"all": []},
            "pinned": {"all": [{"kind": "cohort", "cohort": "base"}], "dataset": "d@3"},
        },
    }
    assert refusals(document) == []


# --- Loading goes on, merges and caps its refusals ---------------------------------------------


def test_loading_goes_on_past_refused_references() -> None:
    document = with_clause({"kind": "value", "column": "t.c", "values": ["$nope"], "extra": 1})
    document["params"] = {"spare": 1}
    result = load(document)
    assert [(r.code, r.path) for r in result.refusals] == [
        ("UNKNOWN_MEMBER", "/cohorts/c/all/0/extra"),
        ("UNKNOWN_PARAMETER", "/cohorts/c/all/0/values/0"),
    ]
    assert result.params_unused == ["spare"]


def test_nothing_is_reported_under_a_refused_position() -> None:
    document = with_clause({"kind": "value", "column": "t.c", "values": "$nope"})
    assert refusals(document) == [("UNKNOWN_PARAMETER", "/cohorts/c/all/0/values")]
    document = with_clause({"kind": "value", "column": "t.c", "values": [None]})
    document["notes"] = None
    assert refusals(document) == [
        ("NULL_NOT_ALLOWED", "/cohorts/c/all/0/values/0"),
        ("NULL_NOT_ALLOWED", "/notes"),
    ]


def test_repeated_problems_in_a_parameter_value_are_reported_once() -> None:
    document = with_clause({"kind": "value", "column": "t.c", "values": "$v"})
    document["params"] = {"v": [[], [], []]}
    assert refusals(document) == [("WRONG_TYPE", "/cohorts/c/all/0/values")]


def test_refusals_are_capped() -> None:
    result = load(with_clause({"kind": "value", "column": "t.c", "values": [[]] * 1200}))
    assert len(result.refusals) == MAX_REFUSALS + 1
    *kept, last = result.refusals
    assert {refusal.code for refusal in kept} == {"WRONG_TYPE"}
    assert (last.code, last.path) == ("LIMIT_EXCEEDED", None)
    assert last.limit is not None
    assert (last.limit.name, last.limit.max) == ("refusals", MAX_REFUSALS)
    assert last.message[0].model_dump() == {"text": "200 more refusals were left out"}


def test_positions_filled_by_parameters_are_reported() -> None:
    assert load(SITES).positions == {
        ("cohorts", "failed", "all", 1, "where", 0, "range", "lt"): "min_score",
        ("cohorts", "failed", "all", 1, "where", 1, "not", "values"): "regions",
    }


# --- Limits ------------------------------------------------------------------------------------


def _long_cohorts(count: int) -> dict[str, Any]:
    return {f"c{index}": {"all": []} for index in range(count)}


LIMITED: list[tuple[dict[str, Any], str, str, int]] = [
    ({"cohorts": _long_cohorts(7)}, "/cohorts", "cohorts", 6),
    ({"views": [{"analysis": "compare.columns"}] * 9}, "/views", "views", 8),
    ({"params": {f"p{index}": 1 for index in range(257)}}, "/params", "parameters", 256),
    ({"notes": "x" * 10_001}, "/notes", "note_characters", 10_000),
    ({"drafted_by": "agent:" + "x" * 201}, "/drafted_by", "name_characters", 200),
    ({"packs": {f"p{index}": ">=1" for index in range(17)}}, "/packs", "packs", 16),
    ({"cohorts": {"c" * 65: {"all": []}}}, "/cohorts/" + "c" * 65, "identifier_characters", 64),
    ({"unit": "t" * 257}, "/unit", "identifier_characters", 64),
    ({"unit": "core:" + "a." * 130 + "a"}, "/unit", "reference_characters", 256),
    ({"dataset": "d" * 65}, "/dataset", "identifier_characters", 64),
    (
        {"views": [{"analysis": "compare." + "x" * 65}]},
        "/views/0/analysis",
        "identifier_characters",
        64,
    ),
    ({"drafted_by": "model:" + "m" * 65}, "/drafted_by", "identifier_characters", 64),
    (
        {"cohorts": {"c": {"all": [], "datasets": [f"d{index}" for index in range(65)]}}},
        "/cohorts/c/datasets",
        "datasets",
        64,
    ),
]

LIMITED_LEAVES: list[tuple[dict[str, Any], str, str, int]] = [
    ({"values": list(range(10_001))}, "/values", "list_members", 10_000),
    ({"values": ["x" * 4097]}, "/values/0", "constant_characters", 4096),
    ({"values": [1], "via": [{"rel": "rel:a.b", "dir": "up"}] * 17}, "/via", "path_steps", 16),
    ({"values": [1], "quantifier": ["some"] * 17}, "/quantifier", "path_steps", 16),
    ({"values": [1], "via": {f"d{i}": [] for i in range(65)}}, "/via", "datasets", 64),
    ({"column": "t." + "c" * 65, "values": [1]}, "/column", "identifier_characters", 64),
    (
        {"values": [1], "via": [{"rel": "rel:a." + "b" * 65, "dir": "up"}]},
        "/via/0/rel",
        "identifier_characters",
        64,
    ),
]


@pytest.mark.parametrize(("members", "path", "name", "maximum"), LIMITED)
def test_document_limits_are_named(
    members: dict[str, Any], path: str, name: str, maximum: int
) -> None:
    base: dict[str, Any] = {"aibi": "1", "dataset": "d", "unit": "t", "cohorts": {"c": {"all": []}}}
    [refusal] = [r for r in load({**base, **members}).refusals if r.code == "LIMIT_EXCEEDED"]
    assert refusal.path == path
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == (name, maximum)


@pytest.mark.parametrize(("members", "path", "name", "maximum"), LIMITED_LEAVES)
def test_leaf_limits_are_named(members: dict[str, Any], path: str, name: str, maximum: int) -> None:
    [refusal] = load(with_clause({"kind": "value", "column": "t.c", **members})).refusals
    assert (refusal.code, refusal.path) == ("LIMIT_EXCEEDED", "/cohorts/c/all/0" + path)
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == (name, maximum)


def test_other_leaf_limits_are_named() -> None:
    covered = {"kind": "covered", "table": "m", "scope": {f"s{i}": ["x"] for i in range(17)}}
    ids = {"kind": "ids", "ids": [{"dataset": "d", "key": list(range(17))}]}
    for clause, path, name in [
        (covered, "/scope", "scope_columns"),
        (ids, "/ids/0/key", "key_columns"),
        ({"kind": "ids", "ids": ["d" * 65 + ":1"]}, "/ids/0", "identifier_characters"),
        ({"kind": "p." + "x" * 65}, "/kind", "identifier_characters"),
    ]:
        [refusal] = load(with_clause(clause)).refusals
        assert (refusal.code, refusal.path) == ("LIMIT_EXCEEDED", "/cohorts/c/all/0" + path)
        assert refusal.limit is not None
        assert refusal.limit.name == name


def test_every_length_cap_names_its_limit(
    length_caps: Callable[[object], list[tuple[str, bool]]],
) -> None:
    capped = length_caps(Document)
    assert len(capped) > 25
    assert sorted(where for where, named in capped if not named) == []


# --- Models on their own -----------------------------------------------------------------------


def test_a_dumped_document_loads_unchanged() -> None:
    for original in (SITES, LIBRARY):
        document = load(original).document
        assert document is not None
        dumped = document.model_dump(mode="json")
        assert "negate" not in json.dumps(dumped)
        again = load(dumped).document
        assert again == document


def test_models_refuse_null() -> None:
    with pytest.raises(ValidationError) as raised:
        Document.model_validate(
            {"aibi": "1", "dataset": None, "unit": "t", "cohorts": {"c": {"all": []}}}
        )
    assert raised.value.errors()[0]["type"] == "null_not_allowed"


# --- Cross-dataset rules (SPEC §7.5) -----------------------------------------------------------


def test_cross_dataset_cohorts_need_concepts() -> None:
    leaf = {"kind": "value", "column": "t.c", "values": [1]}
    base: dict[str, Any] = {"aibi": "1", "unit": "core:person"}
    cohort: dict[str, Any] = {"all": [leaf], "datasets": ["a", "b"]}
    assert refusals({**base, "cohorts": {"c": cohort}}) == [
        ("CONCEPT_REQUIRED", "/cohorts/c/all/0/column")
    ]
    assert refusals({**base, "cohorts": {"c": {**cohort, "unmapped": "allow"}}}) == []
    concept_leaf = {**leaf, "column": "core:age_years"}
    assert refusals({**base, "cohorts": {"c": {**cohort, "all": [concept_leaf]}}}) == []
    assert refusals({**base, "unit": "t", "cohorts": {"c": {**cohort, "unmapped": "allow"}}}) == [
        ("CONCEPT_REQUIRED", "/unit")
    ]


def test_via_by_dataset_is_for_cross_dataset_cohorts() -> None:
    leaf = {"kind": "value", "column": "core:age_years", "values": [1], "via": {"a": []}}
    single = with_clause(leaf)
    assert refusals(single) == [("CROSS_DATASET_ONLY", "/cohorts/c/all/0/via")]
    cross: dict[str, Any] = {
        "aibi": "1",
        "unit": "core:person",
        "cohorts": {"c": {"all": [{**leaf, "via": {"x": []}}], "datasets": ["a", "b@2"]}},
    }
    assert refusals(cross) == [("UNKNOWN_DATASET", "/cohorts/c/all/0/via/x")]


def test_views_over_several_datasets_need_a_concept_unit() -> None:
    document: dict[str, Any] = {
        "aibi": "1",
        "unit": "t",
        "cohorts": {"a": {"all": [], "dataset": "d1"}, "b": {"all": [], "dataset": "d2"}},
        "views": [{"analysis": "compare.columns"}],
    }
    assert refusals(document) == [("CONCEPT_REQUIRED", "/unit")]
    assert refusals({**document, "unit": "core:person"}) == []


# --- Round-2 review: caching, nulls and knock-on refusals -------------------------------------


def test_the_path_cache_holds_no_document_text() -> None:
    before = _kept.cache_info()
    for index in range(50):
        member = f"x{index}" + "y" * 10_000
        load(with_clause({"kind": "value", "column": "t.c", "values": [1], member: 1}))
    after = _kept.cache_info()
    assert after.currsize - before.currsize <= 1


def test_many_distinct_unknown_members_share_one_path_shape() -> None:
    clause: dict[str, Any] = {"kind": "value", "column": "t.c", "values": [1]}
    clause.update({f"extra_{index}": 1 for index in range(5_000)})
    before = _kept.cache_info().misses
    result = load(with_clause(clause))
    assert len(result.refusals) == MAX_REFUSALS + 1
    assert _kept.cache_info().misses - before <= 1


def test_the_siblings_of_a_null_member_are_checked() -> None:
    document = with_clause(
        {"kind": "value", "column": "t.c", "values": [1], "units": None, "lift": "loose"}
    )
    assert refusals(document) == [
        ("INVALID_VALUE", "/cohorts/c/all/0/lift"),
        ("NULL_NOT_ALLOWED", "/cohorts/c/all/0/units"),
    ]


def test_models_report_nulls_per_member_without_document_text() -> None:
    with pytest.raises(ValidationError) as raised:
        Document.model_validate({**with_clause({"all": []}), "notes": None})
    [details] = raised.value.errors()
    assert details["loc"] == ("notes",)
    assert "notes" not in details["msg"]
    refusal = refusal_from_error(details, Document)
    assert (refusal.code, refusal.path) == ("NULL_NOT_ALLOWED", "/notes")
    with pytest.raises(ValidationError):
        Document.model_validate({**with_clause({"all": []}), "params": {"p": [None]}})
    with pytest.raises(ValidationError):
        Document.model_validate(with_clause({"kind": "testpack.flag", "q": None}))


def test_dumps_never_write_null_and_keep_names_without_aliases() -> None:
    document = load(with_clause({"not": {"kind": "value", "column": "t.c", "values": [1]}}))
    assert document.document is not None
    clause = document.document.cohorts["c"].all[0]
    assert isinstance(clause, NotClause)
    assert clause.model_dump(by_alias=False) == {
        "not_": {"kind": "value", "column": "t.c", "values": [1]}
    }
    leaf = clause.not_
    assert isinstance(leaf, ValueLeaf)
    assert "units" not in leaf.model_copy(update={"units": None}).model_dump()


def test_knock_on_refusals_are_not_reported() -> None:
    document = with_clause({"kind": "value", "column": "t.c", "values": "$v"})
    document["params"] = {"v": [1, None]}
    assert refusals(document) == [("NULL_NOT_ALLOWED", "/params/v/1")]
    document = with_clause({"kind": "$nope", "column": "t.c", "values": [1]})
    assert refusals(document) == [("UNKNOWN_PARAMETER", "/cohorts/c/all/0/kind")]
    document = with_clause({"kind": "value", "column": "$col", "values": "$v"})
    document["params"] = [1]
    assert refusals(document) == [("WRONG_TYPE", "/params")]


def test_only_a_clause_kind_refused_hides_the_clause() -> None:
    nested = with_clause({"not": {"kind": "$nope", "column": "BAD"}})
    assert refusals(nested) == [("UNKNOWN_PARAMETER", "/cohorts/c/all/0/not/kind")]
    bad_leaf = {"kind": "value", "column": "BAD", "values": [1]}
    root = {**with_clause(bad_leaf), "kind": "$typo"}
    assert refusals(root) == [
        ("INVALID_VALUE", "/cohorts/c/all/0/column"),
        ("UNKNOWN_PARAMETER", "/kind"),
    ]
    named_kind = {
        **with_clause(bad_leaf),
        "cohorts": {"kind": "$nope", "other": {"all": [bad_leaf]}},
    }
    assert refusals(named_kind) == [
        ("UNKNOWN_PARAMETER", "/cohorts/kind"),
        ("INVALID_VALUE", "/cohorts/other/all/0/column"),
    ]
    scope = with_clause({"kind": "covered", "table": "m", "scope": {"kind": "$nope", "g": []}})
    assert refusals(scope) == [
        ("INVALID_VALUE", "/cohorts/c/all/0/scope/g"),
        ("UNKNOWN_PARAMETER", "/cohorts/c/all/0/scope/kind"),
    ]


@pytest.mark.parametrize(
    ("document", "path"),
    [
        (with_clause({"kind": None, "column": "t.c", "values": [1]}), "/cohorts/c/all/0/kind"),
        (
            with_clause({"kind": "value", "column": "t.c", "values": None}),
            "/cohorts/c/all/0/values",
        ),
        (with_clause({"not": None}), "/cohorts/c/all/0/not"),
        (
            with_clause({"kind": "value", "column": "t.c", "range": {"gt": None}}),
            "/cohorts/c/all/0/range/gt",
        ),
        ({**with_clause({"all": []}), "dataset": None}, "/dataset"),
        ({**with_clause({"all": []}), "cohorts": {"c": {"all": [], "dataset": None}}}, None),
        (
            {**with_clause({"kind": "value", "column": "t.c", "values": "$v"}), "params": None},
            "/params",
        ),
        (
            {**with_clause("$p"), "params": {"p": {"kind": None, "column": "t.c", "values": [1]}}},
            "/params/p/kind",
        ),
    ],
)
def test_a_null_left_out_does_not_make_its_object_refused(
    document: dict[str, Any], path: str | None
) -> None:
    """Only the null is reported: not the clause, range or cohort that then lacks the member."""
    [refusal] = load(document).refusals
    assert refusal.code == "NULL_NOT_ALLOWED"
    assert refusal.path == (path or "/cohorts/c/dataset")


def test_problems_beside_a_null_left_out_are_still_reported() -> None:
    leaf = {"kind": "value", "column": "t.c", "values": None, "lift": "loose"}
    assert refusals(with_clause(leaf)) == [
        ("INVALID_VALUE", "/cohorts/c/all/0/lift"),
        ("NULL_NOT_ALLOWED", "/cohorts/c/all/0/values"),
    ]
    duplicates = {"all": [], "datasets": ["a", "a"], "unmapped": None}
    document = {**with_clause({"all": []}), "unit": "core:person", "cohorts": {"c": duplicates}}
    assert refusals(document) == [
        ("DUPLICATE_ENTRY", "/cohorts/c/datasets/1"),
        ("NULL_NOT_ALLOWED", "/cohorts/c/unmapped"),
    ]


def test_references_to_the_longest_relationships_are_accepted() -> None:
    columns = "+".join(f"{index:02d}".rjust(64, "c") for index in range(16))
    step = {"rel": f"rel:{'t' * 64}.{columns}", "dir": "up"}
    assert len(step["rel"]) == 1108
    assert (
        refusals(with_clause({"kind": "value", "column": "t.c", "values": [1], "via": [step]}))
        == []
    )


def test_substituted_values_may_not_nest_too_deep() -> None:
    deep: Any = {"kind": "value", "column": "t.c", "values": [1]}
    for _ in range(58):
        deep = {"not": deep}
    document = with_clause({"all": ["$deep"]})
    document["params"] = {"deep": deep}
    [refusal] = load(document).refusals
    assert (refusal.code, refusal.path) == ("LIMIT_EXCEEDED", "/cohorts/c/all/0/all/0")
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == ("nesting_depth", MAX_DEPTH)


@pytest.mark.parametrize(
    ("members", "path"),
    [
        ({"unit": "a" * 100}, "/unit"),
        ({"dataset": "a" * 100}, "/dataset"),
        ({"drafted_by": "model:" + "a" * 100}, "/drafted_by"),
        ({"drafted_by": "model:a__b"}, "/drafted_by"),
        ({"unit": "t__state"}, "/unit"),
    ],
)
def test_identifiers_inside_references_are_bounded(members: dict[str, Any], path: str) -> None:
    document = {**with_clause({"kind": "value", "column": "t.c", "values": [1]}), **members}
    assert [p for _, p in refusals(document)] == [path]


def test_column_references_are_bounded_per_identifier() -> None:
    for column in ("t" * 65 + ".c", "t.c__state", "t__x.c"):
        clause = {"kind": "value", "column": column, "values": [1]}
        assert refusals(with_clause(clause)) != [], column


# --- Knock-ons are exactly those that follow from a null (review round 4) -----------------------

_V = {"kind": "value", "column": "t.c", "values": [1]}
_VIA = {**_V, "via": {"d": []}}
_BASE: dict[str, Any] = {"aibi": "1", "dataset": "d", "unit": "t"}


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (
            with_clause({**_V, "range": {"gt": 1}, "lift": None}),
            [
                ("CONFLICTING_MEMBERS", "/cohorts/c/all/0"),
                ("NULL_NOT_ALLOWED", "/cohorts/c/all/0/lift"),
            ],
        ),
        (
            with_clause({"kind": "valeu", "column": "t.c", "values": [1], "units": None}),
            [("UNKNOWN_KIND", "/cohorts/c/all/0"), ("NULL_NOT_ALLOWED", "/cohorts/c/all/0/units")],
        ),
        (
            with_clause({"all": [], "any": [], "x": None}),
            [("WRONG_TYPE", "/cohorts/c/all/0"), ("NULL_NOT_ALLOWED", "/cohorts/c/all/0/x")],
        ),
        (
            with_clause(
                {"kind": "value", "column": "t.c", "range": {"gt": 1, "gte": 2, "lt": None}}
            ),
            [
                ("CONFLICTING_MEMBERS", "/cohorts/c/all/0/range"),
                ("NULL_NOT_ALLOWED", "/cohorts/c/all/0/range/lt"),
            ],
        ),
        (
            {
                **_BASE,
                "cohorts": {
                    "a": {"all": [], "dataset": None},
                    "b": {"all": [_VIA], "dataset": "x"},
                },
            },
            [
                ("NULL_NOT_ALLOWED", "/cohorts/a/dataset"),
                ("CROSS_DATASET_ONLY", "/cohorts/b/all/0/via"),
            ],
        ),
        (
            {
                "aibi": "1",
                "unit": "t",
                "cohorts": {"a": {"all": [], "dataset": None}, "b": {"all": []}},
            },
            [("NULL_NOT_ALLOWED", "/cohorts/a/dataset"), ("DATASET_MISSING", "/cohorts/b")],
        ),
        (
            {**_BASE, "dataset": None, "cohorts": {"b": {"all": [_V], "datasets": ["x", "y"]}}},
            [
                ("CONCEPT_REQUIRED", "/cohorts/b/all/0/column"),
                ("NULL_NOT_ALLOWED", "/dataset"),
                ("CONCEPT_REQUIRED", "/unit"),
            ],
        ),
        (
            {
                **_BASE,
                "cohorts": {
                    "a": {"all": [], "dataset": None},
                    "b": {"all": [{"kind": "cohort", "cohort": "c"}], "dataset": "x"},
                    "c": {"all": [], "dataset": "y"},
                },
            },
            [
                ("NULL_NOT_ALLOWED", "/cohorts/a/dataset"),
                ("COHORT_MISMATCH", "/cohorts/b/all/0/cohort"),
            ],
        ),
        (
            {
                **_BASE,
                "cohorts": {
                    "a": {"all": [], "dataset": None},
                    "b": {"all": [], "dataset": "x"},
                    "c": {"all": [], "dataset": "y"},
                },
                "views": [{"analysis": "compare.x", "cohorts": ["b", "c"]}],
            },
            [("NULL_NOT_ALLOWED", "/cohorts/a/dataset"), ("CONCEPT_REQUIRED", "/unit")],
        ),
        (
            {
                **_BASE,
                "unit": "core:person",
                "cohorts": {"b": {"all": [_V], "datasets": ["x", "y"], "unmapped": None}},
            },
            [("NULL_NOT_ALLOWED", "/cohorts/b/unmapped")],
        ),
        (
            {
                **_BASE,
                "cohorts": {"b": {"all": [], "dataset": "x"}, "c": {"all": [], "dataset": "y"}},
                "views": [{"analysis": "compare.x", "cohorts": None}],
            },
            [("NULL_NOT_ALLOWED", "/views/0/cohorts")],
        ),
    ],
)
def test_a_null_hides_only_what_follows_from_it(
    document: dict[str, Any], expected: list[tuple[str, str]]
) -> None:
    assert refusals(document) == expected


def test_combinator_names_below_a_leaf_are_not_clauses() -> None:
    leaf = {**_V, "not": {"kind": "$nope"}}
    assert refusals(with_clause(leaf)) == [
        ("UNKNOWN_MEMBER", "/cohorts/c/all/0/not"),
        ("UNKNOWN_PARAMETER", "/cohorts/c/all/0/not/kind"),
    ]
    where = {**_V, "where": [{"kind": "$nope"}]}
    assert ("UNKNOWN_MEMBER", "/cohorts/c/all/0/where") in refusals(with_clause(where))


@pytest.mark.parametrize("name", ["via", "scope", "packs", "params", "cohorts"])
def test_a_cohort_named_like_a_map_still_drops_its_nulls(name: str) -> None:
    document = {
        **_BASE,
        "cohorts": {
            name: {"all": [], "notes": None},
            "b": {"all": [{"kind": "cohort", "cohort": "x"}]},
        },
    }
    assert refusals(document) == [
        ("UNKNOWN_COHORT", "/cohorts/b/all/0/cohort"),
        ("NULL_NOT_ALLOWED", f"/cohorts/{name}/notes"),
    ]


def test_long_keys_above_many_values_are_refused_cheaply() -> None:
    document = {**with_clause({"all": []}), "k" * 1_000_000: [0] * 3000}
    started = time.perf_counter()
    [refusal] = load(document).refusals
    assert time.perf_counter() - started < 5
    assert (refusal.code, refusal.path) == ("LIMIT_EXCEEDED", "")
    assert refusal.limit is not None
    assert refusal.limit.name == "pointer_characters"
    wide = {**with_clause({"all": []}), "k" * 16_000: {f"a{i}": 1 for i in range(30_000)}}
    [refusal] = load(wide).refusals
    assert refusal.limit is not None
    assert refusal.limit.name == "all_pointer_characters"


def test_concepts_are_in_the_core_or_a_pack_namespace() -> None:
    for unit in ("summary:x", "value:x"):
        assert refusals({**with_clause({"all": []}), "unit": unit}) == [("INVALID_VALUE", "/unit")]
    view = {"analysis": "core.x"}
    assert refusals({**with_clause({"all": []}), "views": [view]}) == [
        ("INVALID_VALUE", "/views/0/analysis")
    ]


def test_models_built_in_python_hold_only_what_json_carries() -> None:
    document = with_clause({"kind": "value", "column": "t.c", "values": [2.0, 1.5]})
    leaf = Document.model_validate(document).cohorts["c"].all[0]
    assert isinstance(leaf, ValueLeaf)
    assert leaf.values is not None
    assert [type(value) for value in leaf.values] == [int, float]
    for values in ([1e16], ["caf\udce9"]):
        with pytest.raises(ValidationError):
            Document.model_validate(
                with_clause({"kind": "value", "column": "t.c", "values": values})
            )
    with pytest.raises(ValidationError):
        Document.model_validate({**with_clause({"all": []}), "params": {"p": [math.nan]}})


# --- Round 5: substitution caps, namespaces, knock-ons and JSON safety ----------------------------


def _limits(result: DocumentResult) -> set[str]:
    return {refusal.limit.name for refusal in result.refusals if refusal.limit is not None}


def test_substitution_keeps_the_paths_to_all_values_bounded() -> None:
    leaf = {"kind": "value", "column": "t.c", "values": [[]] * 775}
    document = {
        "aibi": "1",
        "dataset": "d",
        "unit": "t",
        "params": {"leaf": leaf},
        "cohorts": {"k" * 16_340: {"all": ["$leaf"] * 256}},
    }
    start = time.perf_counter()
    result = load(document)
    assert time.perf_counter() - start < 2.0
    assert "all_pointer_characters" in _limits(result)


def test_substitution_keeps_each_path_bounded() -> None:
    # The pointer to "$v" is "/cohorts/c/all/0/" and the key, 17 + K characters; the value's own
    # longest pointer is "/a", 2 more.
    def document(key: int) -> dict[str, Any]:
        document = with_clause({"kind": "p.leaf", "k" * key: "$v"})
        return {**document, "params": {"v": {"a": 1}}}

    assert "pointer_characters" not in _limits(load(document(MAX_POINTER - 19)))
    assert "pointer_characters" in _limits(load(document(MAX_POINTER - 18)))


@pytest.mark.parametrize(
    "document",
    [
        {"aibi": "1", "dataset": "d", "unit": "value", "cohorts": {"c": {"all": []}}},
        {"aibi": "1", "dataset": "d", "unit": "summary", "cohorts": {"c": {"all": []}}},
        with_clause(
            {"kind": "exists", "table": "summary", "via": [{"rel": "rel:summary.t", "dir": "down"}]}
        ),
        with_clause({"kind": "value", "column": "cohort.value", "values": [1]}),
    ],
)
def test_table_ids_may_equal_reserved_words(document: dict[str, Any]) -> None:
    assert refusals(document) == []
    for name in ("document.schema.json", "document.as-written.schema.json"):
        assert jsonschema.Draft202012Validator(SCHEMAS[name]()).is_valid(document), name


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (
            {
                **with_clause(
                    {
                        "kind": "$k",
                        "table": "s",
                        "where": [{"kind": "$nope", "column": "BAD"}],
                    }
                ),
                "params": {"k": "exists"},
            },
            [("UNKNOWN_PARAMETER", "/cohorts/c/all/0/where/0/kind")],
        ),
        (
            with_clause({"kind": None, "not": {"kind": "$nope", "column": "BAD"}}),
            [
                ("NULL_NOT_ALLOWED", "/cohorts/c/all/0/kind"),
                ("UNKNOWN_PARAMETER", "/cohorts/c/all/0/not/kind"),
            ],
        ),
    ],
)
def test_a_refused_kind_hides_its_clause_in_the_substituted_document(
    document: dict[str, Any], expected: list[tuple[str, str]]
) -> None:
    assert refusals(document) == expected


@pytest.mark.parametrize(
    ("clause_or_cohort", "expected"),
    [
        (
            {"kind": "value", "column": "t.c", "values": ["x"], "range": {"gt": 1}, "op": None},
            [
                ("CONFLICTING_MEMBERS", "/cohorts/c/all/0"),
                ("NULL_NOT_ALLOWED", "/cohorts/c/all/0/op"),
            ],
        ),
        (
            {"kind": "value", "column": "t.c", "values": ["x"], "range": {"gt": 1}, "value": None},
            [
                ("CONFLICTING_MEMBERS", "/cohorts/c/all/0"),
                ("NULL_NOT_ALLOWED", "/cohorts/c/all/0/value"),
            ],
        ),
        (
            {"kind": "value", "column": "t.c", "range": {"gt": 1, "gte": 2, "value": None}},
            [
                ("CONFLICTING_MEMBERS", "/cohorts/c/all/0/range"),
                ("NULL_NOT_ALLOWED", "/cohorts/c/all/0/range/value"),
            ],
        ),
        (
            {"kind": "exists", "table": "s", "min_count": 2, "quantifier": "every", "values": None},
            [
                ("CONFLICTING_MEMBERS", "/cohorts/c/all/0"),
                ("NULL_NOT_ALLOWED", "/cohorts/c/all/0/values"),
            ],
        ),
        # Controls: here the conflict follows only from the member left out.
        (
            {"kind": "value", "column": "t.c", "values": None},
            [("NULL_NOT_ALLOWED", "/cohorts/c/all/0/values")],
        ),
        (
            {"kind": "value", "column": "t.c", "op": "=", "value": None},
            [("NULL_NOT_ALLOWED", "/cohorts/c/all/0/value")],
        ),
        (
            {"kind": "value", "column": "t.c", "values": None, "range": {"gt": 1}, "op": "="},
            [
                ("CONFLICTING_MEMBERS", "/cohorts/c/all/0"),
                ("NULL_NOT_ALLOWED", "/cohorts/c/all/0/values"),
            ],
        ),
    ],
)
def test_conflicts_are_hidden_only_when_a_null_explains_them(
    clause_or_cohort: dict[str, Any], expected: list[tuple[str, str]]
) -> None:
    assert refusals(with_clause(clause_or_cohort)) == expected


def test_cohorts_with_dataset_and_datasets_conflict_beside_a_null() -> None:
    document = {
        "aibi": "1",
        "unit": "core:person",
        "cohorts": {"c": {"all": [], "dataset": "x", "datasets": ["x", "y"], "unmapped": None}},
    }
    assert ("CONFLICTING_MEMBERS", "/cohorts/c") in refusals(document)


def test_known_cohorts_of_a_view_may_span_datasets_beside_an_unknown_one() -> None:
    document = {
        "aibi": "1",
        "dataset": "w",
        "unit": "t",
        "cohorts": {
            "a": {"all": [], "dataset": None},
            "b": {"all": [], "dataset": "x"},
            "c": {"all": [], "dataset": "y"},
        },
        "views": [{"analysis": "compare.x", "cohorts": ["a", "b", "c"]}],
    }
    assert refusals(document) == [
        ("NULL_NOT_ALLOWED", "/cohorts/a/dataset"),
        ("CONCEPT_REQUIRED", "/unit"),
    ]
    # The known cohorts share one dataset; a's is unknown, whatever the root's is.
    document["cohorts"]["c"]["dataset"] = "x"
    assert refusals(document) == [("NULL_NOT_ALLOWED", "/cohorts/a/dataset")]


@pytest.mark.parametrize(
    "cohorts",
    [
        # a's datasets are unknown: its reference cannot be compared, though the root's differ.
        {
            "a": {"all": [], "dataset": None},
            "b": {"all": [{"kind": "cohort", "cohort": "a"}], "dataset": "y"},
        },
        # a's datasets are unknown: whether it is cross-dataset, and so may use via by dataset,
        # is unknown too.
        {
            "a": {
                "all": [
                    {
                        "kind": "value",
                        "column": "core:age_years",
                        "values": [1],
                        "via": {"z": []},
                    }
                ],
                "dataset": None,
            }
        },
    ],
)
def test_checks_skip_cohorts_whose_datasets_a_null_left_unknown(cohorts: dict[str, Any]) -> None:
    document = {"aibi": "1", "dataset": "x", "unit": "t", "cohorts": cohorts}
    assert refusals(document) == [("NULL_NOT_ALLOWED", "/cohorts/a/dataset")]


def test_a_cohorts_own_dataset_is_known_when_the_root_dataset_is_null() -> None:
    clause = {"kind": "value", "column": "t.c", "values": [1], "via": {"z": []}}
    document = {
        "aibi": "1",
        "dataset": None,
        "unit": "t",
        "cohorts": {"a": {"all": [clause], "dataset": "x"}},
    }
    assert refusals(document) == [
        ("CROSS_DATASET_ONLY", "/cohorts/a/all/0/via"),
        ("NULL_NOT_ALLOWED", "/dataset"),
    ]


def test_a_null_scope_value_is_refused_without_emptying_the_scope() -> None:
    clause = {
        "kind": "covered",
        "table": "s",
        "scope": {"c": None},
        "via": [{"rel": "rel:s.t", "dir": "down"}],
    }
    assert refusals(with_clause(clause)) == [("NULL_NOT_ALLOWED", "/cohorts/c/all/0/scope/c")]


def test_refusals_sort_as_their_pointers_do() -> None:
    # "!" sorts before "/", so /cohorts! comes before anything under /cohorts.
    document = {**with_clause({"kind": "value", "column": "t.c", "values": [1], "x": 1})}
    document["cohorts!"] = 1
    assert refusals(document) == [
        ("UNKNOWN_MEMBER", "/cohorts!"),
        ("UNKNOWN_MEMBER", "/cohorts/c/all/0/x"),
    ]


def test_refusals_at_one_path_are_merged_only_with_the_same_code() -> None:
    document = {
        **with_clause({"all": []}),
        "views": [{"analysis": "compare.x", "cohorts": ["z", "z"]}],
    }
    found = refusals(document)
    assert ("UNKNOWN_COHORT", "/views/0/cohorts/1") in found
    assert ("DUPLICATE_ENTRY", "/views/0/cohorts/1") in found


def test_a_refusal_without_a_path_is_kept_apart_from_one_at_the_root() -> None:
    def refusal(path: str | None) -> Refusal:
        return Refusal(code=RefusalCode.WRONG_TYPE, path=path, message=[])

    found = [
        _Found(None, RefusalCode.WRONG_TYPE, lambda at: refusal(at)),
        _Found((), RefusalCode.WRONG_TYPE, lambda at: refusal(at)),
    ]
    assert [r.path for r in _finish(found, _Positions())] == [None, ""]


def test_pack_leaves_hold_their_members_as_json_text_would() -> None:
    document = Document.model_validate(with_clause({"kind": "p.leaf", "n": 2.0, "m": [1.5, 3.0]}))
    leaf = document.cohorts["c"].all[0]
    assert isinstance(leaf, PackLeaf)
    assert json.dumps(leaf.model_dump()) == '{"kind": "p.leaf", "n": 2, "m": [1.5, 3]}'


@pytest.mark.parametrize(
    "drafted_by", ["agent:caf\udce9", "agent:x\ufffey", "operator:x\u0085y", "agent:x\u009by"]
)
def test_drafted_by_is_unicode_text_without_controls(drafted_by: str) -> None:
    with pytest.raises(ValidationError):
        Document.model_validate({**with_clause({"all": []}), "drafted_by": drafted_by})


@pytest.mark.parametrize("key", ["caf\udce9", "a\ufffe", "a\ufdd0"])
def test_view_parameter_keys_are_unicode_text(key: str) -> None:
    view = {"analysis": "compare.x", "params": {key: 1}}
    with pytest.raises(ValidationError):
        Document.model_validate({**with_clause({"all": []}), "views": [view]})


def test_infinite_constants_are_refused_as_not_finite() -> None:
    with pytest.raises(ValidationError) as raised:
        Document.model_validate(
            with_clause({"kind": "value", "column": "t.c", "values": [math.inf]})
        )
    assert [error["type"] for error in raised.value.errors()] == ["non_finite_number"]


@pytest.mark.parametrize("model", [Document, Refusal])
def test_serialisation_schemas_are_typed_like_validation_schemas(model: type[BaseModel]) -> None:
    adapter: Any = TypeAdapter(model)
    assert adapter.json_schema(mode="serialization") == adapter.json_schema(mode="validation")


# --- Round 6: the parameter cap, exact conflicts, text built in code, caps at their edge -------


def _params(count: int) -> dict[str, Any]:
    return {f"p{index}": index for index in range(count)}


def test_too_many_parameters_are_refused_before_substitution() -> None:
    leaf = {"kind": "value", "column": "t.c", "values": ["$zz"]}
    document = {**with_clause({"all": [leaf] * 256}), "params": _params(20_000)}
    start = time.perf_counter()
    result = load(document)
    assert time.perf_counter() - start < 3.0
    [refusal] = result.refusals
    assert (refusal.code, refusal.path) == ("LIMIT_EXCEEDED", "/params")
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == ("parameters", MAX_PARAMS)
    assert result.params_unused == []
    at_cap = {**with_clause({"all": []}), "params": _params(MAX_PARAMS)}
    assert [code for code, _ in refusals(at_cap)] == []


def test_unknown_parameters_list_the_nearest_declared_names() -> None:
    document = {**with_clause({"kind": "value", "column": "t.c", "values": ["$zz"]})}
    document["params"] = _params(MAX_PARAMS)
    [refusal] = load(document).refusals
    assert refusal.code == "UNKNOWN_PARAMETER"
    listed = [segment.model_dump()["data"] for segment in refusal.alternatives]
    assert listed == sorted(_params(MAX_PARAMS))[-NEAREST:]
    assert f"{MAX_PARAMS} are declared" in json.dumps(refusal.model_dump()["message"])


_LEAF = {"kind": "value", "column": "t.c"}
_CONFLICT = ("CONFLICTING_MEMBERS", "/cohorts/c/all/0")


@pytest.mark.parametrize(
    ("members", "conflict"),
    [
        # No way of giving or omitting the null member fixes these, so the conflict stands.
        ({"values": [1], "value": 1, "op": None}, True),
        ({"value": 1, "values": None}, True),
        ({"op": None}, True),
        ({"range": {"gt": 1}, "value": 1, "op": None}, True),
        ({"values": [1], "op": "=", "value": None}, True),
        ({"op": "=", "lift": None}, True),
        ({"lift": None}, True),
        # Giving or omitting the null members removes any conflict: only the nulls are reported.
        ({"value": 1, "op": None}, False),
        ({"values": None}, False),
        ({"op": "=", "value": None}, False),
        ({"range": None, "op": None, "value": None}, False),
        ({"values": [1], "range": None}, False),
        ({"op": None, "value": None}, False),
    ],
)
def test_a_conflict_beside_a_null_is_reported_when_no_fix_of_the_null_removes_it(
    members: dict[str, Any], conflict: bool
) -> None:
    found = refusals(with_clause({**_LEAF, **members}))
    nulls = [path for code, path in found if code == "NULL_NOT_ALLOWED"]
    assert nulls == sorted(f"/cohorts/c/all/0/{name}" for name, v in members.items() if v is None)
    assert (_CONFLICT in found) is conflict


@pytest.mark.parametrize(
    "document",
    [
        {**with_clause({"all": []}), "notes": "a\ufffe"},
        {**with_clause({"all": []}), "cohorts": {"c": {"all": [], "notes": "a\ufdd0"}}},
        {**with_clause({"all": []}), "views": [{"analysis": "compare.x", "note": "\ufffe"}]},
        with_clause({"kind": "ids", "ids": ["d:a\ufffe"]}),
        with_clause({"kind": "p.leaf", "q\ufffe": 1}),
        with_clause({"kind": "p.leaf", "q\ufdd0": 1}),
    ],
)
def test_text_built_in_code_is_unicode_text(document: dict[str, Any]) -> None:
    """What JSON text could not carry is refused in code too (§7.1)."""
    with pytest.raises(ValidationError):
        Document.model_validate(document)


def _containers(value: Any) -> Iterator[Any]:
    """The arrays and objects in a JSON value, the value included."""
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict | list):
            yield current
            pending.extend(current.values() if isinstance(current, dict) else current)


def test_each_position_a_parameter_fills_has_its_own_copy() -> None:
    """No two positions a parameter fills share an array or object, down to the deepest, nor
    share one with the parameter's value as used or as written."""
    document = {
        **with_clause({"kind": "p.leaf", "a": "$v", "b": "$v"}),
        "params": {"v": {"x": [1, [2, {"y": [3]}]]}},
    }
    result = load(document)
    assert result.document is not None
    leaf = result.document.cohorts["c"].all[0]
    assert isinstance(leaf, PackLeaf)
    extra = leaf.model_extra or {}
    assert extra["a"] == extra["b"] == document["params"]["v"]
    written: Any = result.written
    groups = [extra["a"], extra["b"], result.params_used["v"], written["params"]["v"]]
    seen: dict[int, int] = {}
    for index, group in enumerate(groups[:3]):
        for container in _containers(group):
            assert seen.setdefault(id(container), index) == index
    for container in _containers(groups[3]):
        assert seen.get(id(container), 2) == 2  # used and as written may be one value


def test_the_parsed_context_skips_copying_json_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """A loaded document's JSON values are JSON-safe already and are not copied again; those
    of a document built in code are."""
    calls: list[object] = []
    real = document_module.json_value

    def counted(value: Any) -> Any:
        calls.append(value)
        return real(value)

    monkeypatch.setattr(document_module, "json_value", counted)
    document = {
        **with_clause({"kind": "p.leaf", "a": "$v", "b": {"z": [1]}}),
        "params": {"v": {"x": [1]}},
    }
    assert load(document).document is not None
    assert calls == []
    Document.model_validate({**document, "params": {"v": 1}})
    assert calls != []


def test_drafted_by_is_never_substituted() -> None:
    document = {**with_clause({"all": []}), "params": {"x": "agent:someone"}, "drafted_by": "$x"}
    result = load(document)
    assert [(r.code, r.path) for r in result.refusals] == [("INVALID_VALUE", "/drafted_by")]
    assert result.params_unused == ["x"]


def test_a_long_view_parameter_key_names_its_limit() -> None:
    view = {"analysis": "compare.x", "params": {"k" * 5_000: 1}}
    result = load({**with_clause({"all": []}), "views": [view]})
    assert _limits(result) == {"constant_characters"}


def test_over_the_cap_the_first_refusals_by_path_are_returned() -> None:
    leaves = [{"kind": "value", "column": "t.c", "values": [[]] * 600} for _ in range(2)]
    result = load(with_clause({"all": leaves}))
    paths = sorted(
        f"/cohorts/c/all/0/all/{leaf}/values/{index}" for leaf in range(2) for index in range(600)
    )
    assert [r.path for r in result.refusals[:MAX_REFUSALS]] == paths[:MAX_REFUSALS]
    assert result.refusals[MAX_REFUSALS].code == "LIMIT_EXCEEDED"


def _counts(document: Any) -> tuple[int, int, int]:
    """JSON values, their pointers' length together, and the longest, as parse_json counts."""
    values = total = longest = 0
    pending: list[tuple[Any, int]] = [(document, 0)]
    while pending:
        value, length = pending.pop()
        values, total, longest = values + 1, total + length, max(longest, length)
        if isinstance(value, dict):
            pending.extend((m, length + 1 + len(escape_token(k))) for k, m in value.items())
        elif isinstance(value, list):
            pending.extend((m, length + 1 + len(str(i))) for i, m in enumerate(value))
    return values, total, longest


def _hit(document: dict[str, Any]) -> set[str]:
    return _limits(load_document(json.dumps(document, separators=(",", ":"))))


def test_the_paths_as_written_count_toward_the_substituted_total() -> None:
    key = "k" * 16_000
    document = {"params": {"v": [0] * 1_900}, "x": {key: [0] * 2_600}, "y": {key: ["$v"]}}
    assert _counts(document)[1] < MAX_POINTERS
    assert "all_pointer_characters" in _hit(document)


def test_the_substituted_total_is_exact_at_the_cap() -> None:
    key = "~/" * 2_000 + "k" * 8_000  # 12,000 characters, 16,000 as a pointer token

    def total(n: int) -> int:
        return _counts({"params": {"v": [0] * n}, key: [[0] * n]})[1]

    low, high = 1, 10_000
    while low < high:  # the most values that leave room for a filler member
        middle = (low + high + 1) // 2
        low, high = (middle, high) if total(middle) + 2 <= MAX_POINTERS else (low, middle - 1)
    filler = MAX_POINTERS - total(low) - 1  # a member "F…": 0 adds 1 + its length
    for extra in (0, 1):
        document = {"params": {"v": [0] * low}, key: ["$v"], "F" * (filler + extra): 0}
        assert ("all_pointer_characters" in _hit(document)) is bool(extra)


def test_the_longest_substituted_path_is_exact_at_the_cap() -> None:
    inner = "a~b/" * 10  # 40 characters, 60 as a token
    value = {"z": 0, inner: list(range(12))}  # the longest inside: "/" + 60 + "/11", not the last
    for extra in (0, 1):
        # The pointer to "$v" is "/" + the outer key + "/0"; with 64 inside, 16,384 (+ extra).
        outer = "~" * 100 + "x" * (MAX_POINTER - 64 - 3 + extra - 200)
        document = {"params": {"v": value}, outer: ["$v"]}
        assert ("pointer_characters" in _hit(document)) is bool(extra)


def test_the_substituted_values_are_exact_at_the_cap() -> None:
    value = [0] * 999  # 1,000 values
    for extra in (0, 1):
        document: dict[str, Any] = {"params": {"v": value}, "r": ["$v"] * 150}
        values, _, _ = _counts({"params": {"v": value}, "r": [value] * 150})
        document["pad"] = [0] * (MAX_VALUES - values - 1 + extra)
        assert ("json_values" in _hit(document)) is bool(extra)


def test_the_substituted_depth_is_exact_at_the_cap() -> None:
    value: Any = 0
    for _ in range(MAX_DEPTH - 2):  # as deep as a value in params may be
        value = [value]
    document = {**with_clause({"all": []}), "params": {"w": value}}
    assert "nesting_depth" not in _hit({**document, "x": ["$w"]})
    assert "nesting_depth" in _hit({**document, "x": [["$w"]]})


# --- Round 7: what a refused params hides, maps over their cap, text beyond ASCII -------------

_OVER = {**_params(MAX_PARAMS)}


@pytest.mark.parametrize(
    "extra", [{"n": None}, {"1bad": 1}, {"n": None, "1bad": 1}, {"n": {"m": [None]}}]
)
def test_nothing_inside_params_over_the_cap_is_reported(extra: dict[str, Any]) -> None:
    """One over the cap, with a null or a bad name among the entries: params alone is refused,
    and the reference is not looked up (§8.6)."""
    leaf = {"kind": "value", "column": "t.c", "values": ["$zz"]}
    document = {**with_clause(leaf), "params": {**_OVER, **extra}}
    assert refusals(document) == [("LIMIT_EXCEEDED", "/params")]


def test_nothing_inside_params_that_is_not_an_object_is_reported() -> None:
    document = {**with_clause({"all": []}), "params": [None, {"a": None}]}
    assert refusals(document) == [("WRONG_TYPE", "/params")]
    assert refusals({**with_clause({"all": []}), "params": None}) == [
        ("NULL_NOT_ALLOWED", "/params")
    ]


@pytest.mark.parametrize("params", [{**_OVER, "extra": 1}, [1], None])
def test_malformed_references_are_refused_whatever_params_is(params: Any) -> None:
    leaves = [
        {"kind": "value", "column": "t.c", "values": [value]}
        for value in ("$1bad", "$", "$a-b", "$fine")
    ]
    found = refusals({**with_clause({"all": leaves}), "params": params})
    assert [path for code, path in found if code == "INVALID_PARAMETER_REFERENCE"] == [
        f"/cohorts/c/all/0/all/{index}/values/0" for index in range(3)
    ]
    assert [path for code, path in found if code != "INVALID_PARAMETER_REFERENCE"] == ["/params"]


def test_unknown_parameters_list_every_declared_name_in_order_when_few() -> None:
    document = {
        **with_clause({"kind": "value", "column": "t.c", "values": ["$zz"]}),
        "params": {"b": 1, "a": 2},
    }
    [refusal] = load(document).refusals
    assert [segment.model_dump()["data"] for segment in refusal.alternatives] == ["a", "b"]
    assert "declared" not in json.dumps(refusal.model_dump()["message"])


def test_the_nearest_names_are_a_window_in_sorted_order() -> None:
    declared = sorted(f"n{index:03d}" for index in range(100))
    assert nearest("n050x", declared) == declared[43:59]
    assert nearest("a", declared) == declared[:NEAREST]
    assert nearest("z", declared) == declared[-NEAREST:]
    assert nearest("x", declared[:NEAREST]) == declared[:NEAREST]


def test_many_unknown_references_stay_small() -> None:
    """A thousand refusals each list at most NEAREST names, however many are declared."""
    params = {f"{'k' * 61}{index:03d}": index for index in range(MAX_PARAMS)}  # 64 characters
    leaf = {"kind": "value", "column": "t.c", "values": [f"$u{index}" for index in range(1_000)]}
    result = load({**with_clause(leaf), "params": params})
    assert len(result.refusals) == MAX_REFUSALS
    assert {refusal.code for refusal in result.refusals} == {"UNKNOWN_PARAMETER"}
    assert all(len(refusal.alternatives) == NEAREST for refusal in result.refusals)
    # Listing all 256 names made 18.7 MiB.
    assert len(json.dumps([r.model_dump(mode="json") for r in result.refusals])) < 2_000_000


def test_a_conflict_beside_many_unrelated_nulls_is_found_quickly() -> None:
    """Only the members of a conflict's rule are tried as fixes, not every member lost (trying
    every subset of these 26 would take a minute)."""
    nulls = {f"x{index}": None for index in range(26)}
    start = time.perf_counter()
    found = refusals(with_clause({**_LEAF, "op": "=", **nulls}))
    assert time.perf_counter() - start < 2.0
    assert _CONFLICT in found
    assert len([code for code, _ in found if code == "NULL_NOT_ALLOWED"]) == 26


def _map_over_cap(where: str) -> tuple[dict[str, Any], str, str]:
    """A document with a map one entry over its cap and one bad entry, the map's pointer and
    the limit's name."""
    if where == "cohorts":
        cohorts: dict[str, Any] = {f"c{index}": {"all": []} for index in range(MAX_COHORTS)}
        cohorts["bad"] = {"all": 1}
        return {**with_clause({"all": []}), "cohorts": cohorts}, "/cohorts", "cohorts"
    if where == "packs":
        packs: dict[str, Any] = {f"p{index}": ">=1" for index in range(MAX_PACKS)}
        packs["q"] = "not a specifier"
        return {**with_clause({"all": []}), "packs": packs}, "/packs", "packs"
    if where == "scope":
        scope: dict[str, Any] = {f"t.c{index}": ["a"] for index in range(MAX_COLUMNS)}
        scope["t.bad"] = []
        leaf = {"kind": "covered", "table": "t2", "scope": scope}
        return with_clause(leaf), "/cohorts/c/all/0/scope", "scope_columns"
    via: dict[str, Any] = {f"d{index}": [] for index in range(MAX_DATASETS)}
    via["bad"] = 1
    leaf = {"kind": "value", "column": "t.c", "values": [1], "via": via}
    return with_clause(leaf), "/cohorts/c/all/0/via", "datasets"


@pytest.mark.parametrize("where", ["cohorts", "packs", "scope", "via"])
def test_a_map_over_its_cap_is_refused_for_its_size_whatever_its_entries(where: str) -> None:
    """As a list is: Pydantic checks a dict's length only when every entry is valid."""
    document, at, name = _map_over_cap(where)
    result = load(document)
    over = [r for r in result.refusals if r.path == at]
    assert [r.code for r in over] == ["LIMIT_EXCEEDED"]
    assert over[0].limit is not None
    assert over[0].limit.name == name
    assert not [r for r in result.refusals if r.path and r.path.startswith(at + "/")]
    with pytest.raises(ValidationError) as raised:
        Document.model_validate(document)
    assert "too_long" in [error["type"] for error in raised.value.errors()]


_TEXTS = ["café", "日本語", "\U0001f600", "\ufffd", "\ufdcf", "\ufdf0"]
"""Unicode text beyond ASCII, U+FDCF and U+FDF0 on either side of the noncharacters U+FDD0 to
U+FDEF."""


def _texts_everywhere(value: str) -> dict[str, Any]:
    clauses = [
        {"kind": "value", "column": "t.c", "values": [value]},
        {"kind": "ids", "ids": [f"d:{value}"]},
        {"kind": "p.leaf", value: value},
    ]
    return {
        **with_clause({"all": clauses}),
        "notes": value,
        "drafted_by": f"agent:{value}",
        "cohorts": {"c": {"all": clauses, "notes": value}},
        "views": [{"analysis": "compare.x", "cohorts": ["c"], "note": value, "params": {value: 1}}],
    }


@pytest.mark.parametrize("value", _TEXTS)
def test_text_beyond_ascii_is_accepted_loaded_and_built_in_code(value: str) -> None:
    document = _texts_everywhere(value)
    assert refusals(document) == []
    assert load_document(json.dumps(document, ensure_ascii=False)).refusals == []
    Document.model_validate(document)


@pytest.mark.parametrize(
    "document",
    [
        with_clause({**_LEAF, "values": [1], "lift": None}),
        {**with_clause({"all": []}), "params": {"n": None}},
        SITES,
        with_clause({**_LEAF, "values": [1], "x": 1}),
    ],
)
def test_a_load_leaves_no_reference_cycles(document: dict[str, Any]) -> None:
    """What a load builds is freed when it is dropped, without waiting for the collector."""
    text = json.dumps(document)
    load_document(text)  # warm caches
    gc.collect()
    gc.disable()
    try:
        for _ in range(20):
            load_document(text)
        found = gc.collect()
    finally:
        gc.enable()
    assert found == 0


# --- Round 8: nulls and references inside maps over their cap, the segments of messages -------


def _segments(segments: Any) -> list[dict[str, Any]]:
    return [segment.model_dump() for segment in segments]


def _entries_over_cap(where: str) -> tuple[dict[str, Any], str, str]:
    """A map one valid entry over its cap, with a null entry and a reference to an unknown
    parameter beside them; the map's pointer and the reference's."""
    if where == "cohorts":
        cohorts: dict[str, Any] = {f"c{index}": {"all": []} for index in range(MAX_COHORTS + 1)}
        cohorts.update(n=None, u={"all": ["$zz"]})
        return {**with_clause({"all": []}), "cohorts": cohorts}, "/cohorts", "/cohorts/u/all/0"
    if where == "packs":
        packs: dict[str, Any] = {f"p{index}": ">=1" for index in range(MAX_PACKS + 1)}
        packs.update(n=None, u="$zz")
        return {**with_clause({"all": []}), "packs": packs}, "/packs", "/packs/u"
    if where == "scope":
        scope: dict[str, Any] = {f"t.c{index}": ["a"] for index in range(MAX_COLUMNS + 1)}
        scope.update({"t.n": None, "t.u": ["$zz"]})
        at = "/cohorts/c/all/0/scope"
        return with_clause({"kind": "covered", "table": "t2", "scope": scope}), at, f"{at}/t.u/0"
    via: dict[str, Any] = {f"d{index}": [] for index in range(MAX_DATASETS + 1)}
    via.update(n=None, u="$zz")
    leaf = {"kind": "value", "column": "t.c", "values": [1], "via": via}
    at = "/cohorts/c/all/0/via"
    return with_clause(leaf), at, f"{at}/u"


@pytest.mark.parametrize("where", ["cohorts", "packs", "scope", "via"])
def test_nulls_and_references_inside_a_map_over_its_cap_are_reported(where: str) -> None:
    """They are problems of their own, found before the schema is checked (§8.6)."""
    document, at, reference = _entries_over_cap(where)
    null = at + ("/t.n" if where == "scope" else "/n")
    assert refusals({**document, "params": {"a": 1}}) == [
        ("LIMIT_EXCEEDED", at),
        ("NULL_NOT_ALLOWED", null),
        ("UNKNOWN_PARAMETER", reference),
    ]


def test_a_map_over_its_cap_says_how_many_entries_it_has() -> None:
    cohorts = {f"c{index}": {"all": []} for index in range(MAX_COHORTS + 1)}
    [refusal] = load({**with_clause({"all": []}), "cohorts": cohorts}).refusals
    message = f"Dictionary should have at most {MAX_COHORTS} items, not {MAX_COHORTS + 1}"
    assert _segments(refusal.message) == [{"text": message}]


@pytest.mark.parametrize(("where", "cap"), [("cohorts", MAX_COHORTS), ("packs", MAX_PACKS)])
def test_a_null_entry_counts_toward_a_root_map_s_cap(where: str, cap: int) -> None:
    """Root maps keep their null members, so the map is refused for its size as written."""
    entries: dict[str, Any] = (
        {f"c{index}": {"all": []} for index in range(cap)}
        if where == "cohorts"
        else {f"p{index}": ">=1" for index in range(cap)}
    )
    document = {**with_clause({"all": []}), where: {**entries, "n": None}}
    assert refusals(document) == [
        ("LIMIT_EXCEEDED", f"/{where}"),
        ("NULL_NOT_ALLOWED", f"/{where}/n"),
    ]


def test_a_null_cohort_is_neither_a_bad_map_nor_an_unknown_cohort() -> None:
    cohorts = {"c": {"all": []}, "n": None}
    document = {**with_clause({"all": []}), "cohorts": cohorts}
    assert refusals(document) == [("NULL_NOT_ALLOWED", "/cohorts/n")]
    views = [{"analysis": "compare.x", "cohorts": ["n"]}]
    assert refusals({**document, "views": views}) == [("NULL_NOT_ALLOWED", "/cohorts/n")]


def test_params_over_the_cap_are_refused_in_code_before_their_entries() -> None:
    params = {**_params(MAX_PARAMS), "bad": None}
    with pytest.raises(ValidationError) as raised:
        Document.model_validate({**with_clause({"all": []}), "params": params})
    assert [(error["type"], error["loc"]) for error in raised.value.errors()] == [
        ("too_long", ("params",))
    ]


def test_an_unknown_parameter_is_named_as_data_and_the_note_is_text() -> None:
    """A6: the name comes from the document; the note is the server's."""
    leaf = {"kind": "value", "column": "t.c", "values": ["$zz"]}
    params = {f"p{index:02d}": index for index in range(NEAREST + 1)}
    [refusal] = load({**with_clause(leaf), "params": params}).refusals
    assert _segments(refusal.message) == [
        {"text": "Unknown parameter "},
        {"data": "zz"},
        {"text": f"; {NEAREST + 1} are declared, and the {NEAREST} nearest are listed"},
    ]
    assert len(refusal.alternatives) == NEAREST


def test_a_malformed_reference_is_quoted_as_data() -> None:
    leaf = {"kind": "value", "column": "t.c", "values": ["$1bad"]}
    [refusal] = load(with_clause(leaf)).refusals
    assert _segments(refusal.message)[:2] == [
        {"text": "Not a parameter reference: "},
        {"data": "$1bad"},
    ]


def test_an_unknown_parameter_lists_only_names_a_reference_can_take() -> None:
    """Names that are no parameter names are refused themselves, and never listed."""
    params = {**{f"p{index}": index for index in range(10)}, **{f"p-{i}": i for i in range(20)}}
    leaf = {"kind": "value", "column": "t.c", "values": ["$p"]}
    result = load({**with_clause(leaf), "params": params})
    [unknown] = [r for r in result.refusals if r.code == "UNKNOWN_PARAMETER"]
    assert [segment["data"] for segment in _segments(unknown.alternatives)] == [
        f"p{index}" for index in range(10)
    ]
    assert "declared" not in json.dumps(_segments(unknown.message))
    assert {r.path for r in result.refusals if r.code == "INVALID_VALUE"} == {
        f"/params/p-{index}" for index in range(20)
    }

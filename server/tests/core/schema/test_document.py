"""Documents as written: shape, substitution, whole-document checks and refusal paths."""

import copy
import json
import math
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin

import jsonschema
import pytest
from annotated_types import MaxLen
from pydantic import BaseModel, StringConstraints, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo

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
from aibi.core.schema.limits import (
    MAX_DEPTH,
    MAX_DOCUMENT_BYTES,
    MAX_LIST,
    MAX_POINTER,
    MAX_REFUSALS,
    LimitName,
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


def _annotations(root: type[BaseModel]) -> Iterator[tuple[str, list[object]]]:
    """Every annotation reachable from a model, with its Annotated metadata."""
    seen: set[int] = set()
    pending: list[tuple[str, object, list[object]]] = [(root.__name__, root, [])]
    while pending:
        where, annotation, metadata = pending.pop()
        if id(annotation) in seen and not metadata:
            continue
        seen.add(id(annotation))
        origin = get_origin(annotation)
        if origin is Annotated:
            inner, *extra = get_args(annotation)
            pending.append((where, inner, [*metadata, *extra]))
            continue
        yield where, metadata
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            for name, info in annotation.model_fields.items():
                pending.append((f"{annotation.__name__}.{name}", info.annotation, info.metadata))
        else:
            pending.extend((where, arg, []) for arg in get_args(annotation))


def _caps_length(extra: object) -> bool:
    if isinstance(extra, MaxLen):
        return True
    if isinstance(extra, StringConstraints):
        return extra.max_length is not None
    if isinstance(extra, FieldInfo):
        return any(_caps_length(inner) for inner in extra.metadata)
    return False


def test_every_length_cap_names_its_limit() -> None:
    capped = [
        (where, any(isinstance(extra, LimitName) for extra in metadata))
        for where, metadata in _annotations(Document)
        if any(_caps_length(extra) for extra in metadata)
    ]
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


def test_view_parameter_keys_are_unicode_text() -> None:
    view = {"analysis": "compare.x", "params": {"caf\udce9": 1}}
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

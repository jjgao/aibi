"""Documents as written: shape, substitution, whole-document checks and refusal paths."""

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin

import pytest
from annotated_types import MaxLen
from pydantic import BaseModel, StringConstraints, ValidationError
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
from aibi.core.schema.limits import (
    MAX_DEPTH,
    MAX_DOCUMENT_BYTES,
    MAX_LIST,
    MAX_REFUSALS,
    LimitName,
)
from aibi.core.schema.loading import (
    DocumentResult,
    _kept,  # pyright: ignore[reportPrivateUsage]
    load_document,
    refusal_from_error,
)

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
        "/cohorts/failed/all/1/where/0/range/lt": "min_score",
        "/cohorts/failed/all/1/where/1/not/values": "regions",
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
    ({"unit": "t" * 257}, "/unit", "identifier_characters", 256),
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

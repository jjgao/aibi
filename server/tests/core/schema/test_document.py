"""Documents as written: shape, substitution, whole-document checks and refusal paths."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from aibi.core.schema.document import (
    AllClause,
    ExistsLeaf,
    NotClause,
    PackLeaf,
    UnknownClause,
    ValueLeaf,
)
from aibi.core.schema.limits import MAX_DOCUMENT_BYTES, MAX_LIST
from aibi.core.schema.loading import DocumentResult, load_document

DOCUMENTS = Path(__file__).parent / "documents"


def example(name: str) -> dict[str, Any]:
    """An example document: ``sites`` is non-biomedical (SPEC P8), ``trial`` uses a pack leaf."""
    loaded: dict[str, Any] = json.loads((DOCUMENTS / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


SITES = example("sites")
TRIAL = example("trial")


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


def test_trial_document_loads() -> None:
    result = load(TRIAL)
    assert result.refusals == []
    assert result.document is not None
    clauses = result.document.cohorts["tp53"].all
    assert isinstance(clauses[0], ValueLeaf)
    assert isinstance(clauses[3], AllClause) is False
    unknown = clauses[2]
    assert unknown.model_dump(by_alias=True) == {
        "unknown": {"kind": "onco.genomic", "q": "EGFR: AMP"}
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
                "via": [{"rel": "samples", "dir": "down"}],
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
        ("TP53", [("WRONG_TYPE", "/cohorts/c/all/0")]),
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
        with_clause({"kind": "onco.genomic", "q": "TP53: MUT", "anything": [1, {"x": True}]})
    )
    assert result.document is not None
    leaf = result.document.cohorts["c"].all[0]
    assert isinstance(leaf, PackLeaf)
    assert leaf.model_extra == {"q": "TP53: MUT", "anything": [1, {"x": True}]}


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
    assert refusals({**base, "packs": {"onco": "not a specifier"}}) == [
        ("INVALID_VALUE", "/packs/onco")
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
        ("INVALID_VALUE", "/cohorts/c")
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
    ) == [("INVALID_VALUE", "/views/0")]
    assert refusals(
        {**base, "views": [{"analysis": "compare.columns", "cohorts": ["a", "a"]}]}
    ) == [("INVALID_VALUE", "/views/0")]
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

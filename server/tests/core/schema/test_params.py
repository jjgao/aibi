import json

from pydantic import JsonValue

from aibi.core.schema.limits import MAX_DOCUMENT_BYTES, MAX_VALUES
from aibi.core.schema.params import substitute


def test_exact_references_take_any_type() -> None:
    document: dict[str, JsonValue] = {
        "params": {"branches": ["north", "east"], "days": 3, "rule": {"gte": 3}},
        "a": "$branches",
        "b": ["$days", {"range": "$rule"}],
    }
    result = substitute(document)
    assert not result.refusals
    assert result.document == {
        "params": document["params"],
        "a": ["north", "east"],
        "b": [3, {"range": {"gte": 3}}],
    }
    assert result.used == {"branches": ["north", "east"], "days": 3, "rule": {"gte": 3}}
    assert result.positions == {"/a": "branches", "/b/0": "days", "/b/1/range": "rule"}


def test_no_interpolation_escapes_and_keys() -> None:
    result = substitute({"params": {"x": 1}, "a": "x$x", "b": "$$x", "$x": "kept"})
    assert result.document == {"params": {"x": 1}, "a": "x$x", "b": "$x", "$x": "kept"}
    assert result.unused == ["x"]


def test_params_are_not_substituted_into_themselves() -> None:
    result = substitute({"params": {"a": "$b", "b": 1}, "v": "$a"})
    assert result.document == {"params": {"a": "$b", "b": 1}, "v": "$b"}


def test_text_members_are_never_substituted() -> None:
    document: dict[str, JsonValue] = {
        "params": {"x": 1},
        "notes": "$100 grant",
        "drafted_by": "agent:$x",
        "cohorts": {"c": {"notes": "$$x", "all": ["$x"]}},
        "views": [{"note": "$x"}],
    }
    result = substitute(document)
    assert not result.refusals
    assert result.document == {**document, "cohorts": {"c": {"notes": "$$x", "all": [1]}}}


def test_unknown_and_invalid_references_are_refused_with_paths() -> None:
    result = substitute({"params": {"known": 1}, "a": ["$unknown", "$5.00", "$known", "$known\n"]})
    assert [(r.code, r.path) for r in result.refusals] == [
        ("UNKNOWN_PARAMETER", "/a/0"),
        ("INVALID_PARAMETER_REFERENCE", "/a/1"),
        ("INVALID_PARAMETER_REFERENCE", "/a/3"),
    ]
    assert result.failed == ["/a/0", "/a/1", "/a/3"]
    assert result.refusals[0].alternatives[0].model_dump() == {"data": "known"}
    assert result.document == {"params": {"known": 1}, "a": ["$unknown", "$5.00", 1, "$known\n"]}


def test_params_must_be_an_object() -> None:
    result = substitute({"params": [1], "a": 1})
    assert [(r.code, r.path) for r in result.refusals] == [("WRONG_TYPE", "/params")]


def _refused_tail(params: dict[str, JsonValue], limit: str, uses: int) -> None:
    """References are refused from the one that crosses the limit on, and only those."""
    references: list[JsonValue] = [f"${next(iter(params))}"] * uses
    result = substitute({"params": params, "a": references})
    assert result.refusals
    for refusal in result.refusals:
        assert refusal.code == "LIMIT_EXCEEDED"
        assert refusal.limit is not None
        assert refusal.limit.name == limit
    paths = [refusal.path for refusal in result.refusals]
    first = int(str(paths[0]).rsplit("/", 1)[1])
    assert 0 < first < uses
    assert paths == [f"/a/{index}" for index in range(first, uses)]


def test_the_substituted_document_is_no_larger_than_a_document() -> None:
    big = "x" * 4000
    uses = MAX_DOCUMENT_BYTES // len(json.dumps(big)) + 10
    _refused_tail({"big": big}, "substituted_document_bytes", uses)


def test_the_substituted_document_holds_no_more_values_than_a_document() -> None:
    values: list[JsonValue] = [0] * 1000
    uses = MAX_VALUES // 1000 + 10
    _refused_tail({"v": values}, "json_values", uses)

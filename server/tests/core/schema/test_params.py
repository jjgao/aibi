from pydantic import JsonValue

from aibi.core.schema.params import substitute


def test_exact_references_take_any_type() -> None:
    document: dict[str, JsonValue] = {
        "params": {"genes": ["TP53", "EGFR"], "grade": 3, "rule": {"gte": 3}},
        "a": "$genes",
        "b": ["$grade", {"range": "$rule"}],
    }
    result = substitute(document)
    assert not result.refusals
    assert result.document == {
        "params": document["params"],
        "a": ["TP53", "EGFR"],
        "b": [3, {"range": {"gte": 3}}],
    }
    assert result.used == {"genes": ["TP53", "EGFR"], "grade": 3, "rule": {"gte": 3}}
    assert result.positions == {"/a": "genes", "/b/0": "grade", "/b/1/range": "rule"}


def test_no_interpolation_escapes_and_keys() -> None:
    result = substitute({"params": {"x": 1}, "a": "x$x", "b": "$$x", "$x": "kept"})
    assert result.document == {"params": {"x": 1}, "a": "x$x", "b": "$x", "$x": "kept"}
    assert result.unused == ["x"]


def test_params_are_not_substituted_into_themselves() -> None:
    result = substitute({"params": {"a": "$b", "b": 1}, "v": "$a"})
    assert result.document == {"params": {"a": "$b", "b": 1}, "v": "$b"}


def test_unknown_and_invalid_references_are_refused_with_paths() -> None:
    result = substitute({"params": {"known": 1}, "a": ["$unknown", "$5.00", "$known"]})
    assert [(r.code, r.path) for r in result.refusals] == [
        ("UNKNOWN_PARAMETER", "/a/0"),
        ("INVALID_PARAMETER_REFERENCE", "/a/1"),
    ]
    assert result.refusals[0].alternatives[0].model_dump() == {"data": "known"}


def test_params_must_be_an_object() -> None:
    result = substitute({"params": [1], "a": 1})
    assert [(r.code, r.path) for r in result.refusals] == [("WRONG_TYPE", "/params")]

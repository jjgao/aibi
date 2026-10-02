"""Canonical forms, cohort ids, leaf keys and cohort-count digests (§7.6, §13.4; D281–D284)."""

import json
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from aibi.core.engine.canonical import (
    CanonicalCohort,
    Canonicalisation,
    CohortIdentity,
    ViewIdentity,
    as_document,
    canonicalise,
)
from aibi.core.engine.counts import count_parts
from aibi.core.engine.data import Release
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.ids import derivation_id, hashed, leaf_key
from aibi.core.engine.resolve import ViewPredicate
from aibi.core.schema.analyses import ExistenceParams
from aibi.core.schema.jsonio import canonical
from aibi.core.schema.loading import load_document
from aibi.core.schema.refusals import RefusalCode
from aibi.core.schema.semantics import SEMANTICS_VERSION

City = Callable[..., Release]
Doc = Callable[..., dict[str, Any]]
Canon = Callable[..., Canonicalisation]

ROWS: dict[str, list[dict[str, Any]]] = {
    "owners": [{"owner_id": "o1", "region": "north"}],
    "establishments": [
        {"establishment_id": "e1", "cuisine": "thai", "grade": "A", "seats": 40, "owner_id": "o1"},
        {"establishment_id": "e2", "cuisine": "pizza", "grade": "pending", "seats": 8},
        {"establishment_id": "e3", "grade": "C", "seats": 12, "frontage": 4.5},
    ],
    "inspections": [
        {"inspection_id": "i1", "establishment_id": "e1", "kind": "routine", "score": 90},
        {"inspection_id": "i2", "establishment_id": "e2", "kind": "courtesy", "score": 40},
    ],
    "inspection_checklists": [{"inspection_id": "i1", "checklist": "all"}],
    "checklist_items": [{"checklist": "all", "code": "temp", "all_codes": True}],
    "violations": [{"violation_id": "v1", "inspection_id": "i1", "code": "temp", "severity": 4}],
}


def value(column: str, **predicate: Any) -> dict[str, Any]:
    return {"kind": "value", "column": column, **predicate}


THAI = value("establishments.cuisine", values=["thai"])
SEATS = value("establishments.seats", range={"gt": 10})
TEMP = value("violations.code", values=["temp"])
UNKNOWN_COLUMN = value("establishments.stars", values=[5])


def only(result: Canonicalisation) -> CanonicalCohort:
    assert result.refusals == [], result.refusals
    [cohort] = result.cohorts.values()
    return cohort


def digest_of(cohort: CanonicalCohort) -> str:
    return count_parts(cohort, evaluate(cohort.resolved)).digest


def test_a_cohort_s_form_maps_its_manifest_to_its_clause_tree(
    canon: Canon, city: City, doc: Doc
) -> None:
    release = city(ROWS)
    cohort = only(canon(doc([SEATS, THAI]), release))
    [(manifest, tree)] = cohort.form.items()
    assert manifest == release.manifest
    assert tree == {"all": sorted(cohort.clauses, key=lambda c: canonical(c).decode())}
    single = only(canon(doc([THAI]), release))
    assert single.form == {release.manifest: THAI}
    every = only(canon(doc([]), release))
    assert every.form == {release.manifest: {"all": []}}


def test_order_insensitive_collections_are_sorted_as_utf16_code_units_without_duplicates(
    canon: Canon, city: City, doc: Doc
) -> None:
    # U+FF5E sorts after U+1F600 in UTF-16, whose high surrogate is U+D83D, but before it by
    # code point.
    tilde, face = chr(0xFF5E), chr(0x1F600)
    names = value("establishments.name", values=[tilde, face, tilde, "b"])
    cohort = only(canon(doc([names]), city()))
    assert cohort.clauses == (value("establishments.name", values=["b", face, tilde]),)
    ids = {"kind": "ids", "ids": ["d:e2", "d:e10", "d:e2"]}
    [clause] = only(canon(doc([ids]), city())).clauses
    assert [member["key"] for member in clause["ids"]] == [["e10"], ["e2"]]  # type: ignore[index]
    scope = {"kind": "covered", "table": "violations", "scope": {"code": ["temp", "pest", "temp"]}}
    [covered] = only(canon(doc([scope]), city())).clauses
    assert covered["scope"] == {"code": ["pest", "temp"]}  # type: ignore[index]


EQUIVALENT = [
    pytest.param(
        [value("establishments.cuisine", op="=", value="thai")], [THAI], id="op = and values"
    ),
    pytest.param(
        [value("establishments.cuisine", op="!=", value="thai")],
        [{"not": THAI}],
        id="!= and not",
    ),
    pytest.param(
        [value("establishments.cuisine", values=["thai"], negate=True)],
        [{"not": THAI}],
        id="negate and not",
    ),
    pytest.param([value("establishments.seats", op=">", value=10)], [SEATS], id="op and range"),
    pytest.param([{"all": [THAI]}], [THAI], id="single-member all"),
    pytest.param([{"any": [{"all": [THAI]}]}], [THAI], id="single-member any"),
    pytest.param([THAI, THAI], [THAI], id="repeated clause"),
    pytest.param([{"any": [SEATS, SEATS]}], [SEATS], id="repeated member"),
    pytest.param([SEATS, THAI], [THAI, SEATS], id="reordered clauses"),
    pytest.param([{"all": [SEATS, THAI]}], [THAI, SEATS], id="nested all"),
    pytest.param(
        [value("establishments.cuisine", values=["thai", "pizza"])],
        [value("establishments.cuisine", values=["pizza", "thai", "pizza"])],
        id="reordered values",
    ),
    pytest.param(
        [TEMP],
        [{"kind": "exists", "table": "violations", "where": [TEMP]}],
        id="direct and multi-step",
    ),
    pytest.param(
        [TEMP],
        [
            {
                "kind": "exists",
                "table": "inspections",
                "where": [{"kind": "exists", "table": "violations", "where": [TEMP]}],
            }
        ],
        id="direct and nested",
    ),
    pytest.param(
        [{"kind": "exists", "table": "inspections", "min_count": 2}],
        [{"kind": "exists", "table": "inspections", "quantifier": {"some": 2}}],
        id="min_count and quantifier",
    ),
    pytest.param(
        [{"kind": "exists", "table": "inspections", "where": [{"all": [TEMP]}], "lift": "strict"}],
        [{"kind": "exists", "table": "inspections", "where": [TEMP]}],
        id="default lift",
    ),
    pytest.param(
        [value("establishments.frontage", range={"gte": 4}, units="m")],
        [value("establishments.frontage", range={"gte": 4.0})],
        id="the column's units",
    ),
]


@pytest.mark.parametrize(("first", "second"), EQUIVALENT)
def test_equivalent_syntax_gives_identical_ids_and_digests(
    canon: Canon, city: City, doc: Doc, first: list[Any], second: list[Any]
) -> None:
    release = city(ROWS)
    one, other = only(canon(doc(first), release)), only(canon(doc(second), release))
    assert one.form == other.form
    assert one.id == other.id
    assert digest_of(one) == digest_of(other)


def _keys_reversed(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _keys_reversed(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [_keys_reversed(item) for item in value]
    return value


def test_renames_reordered_cohorts_and_reordered_keys_give_identical_ids_and_digests(
    canon: Canon, city: City, doc: Doc
) -> None:
    release = city(ROWS)
    written = doc({"a": [THAI], "b": [SEATS, TEMP]})
    others = [
        doc({"Other": [TEMP, SEATS], "first_one": [THAI]}),
        _keys_reversed(written),
    ]

    def found(given: dict[str, Any]) -> list[tuple[str, str]]:
        cohorts = canon(given, release).cohorts.values()
        return sorted((cohort.id, digest_of(cohort)) for cohort in cohorts)

    assert all(found(other) == found(written) for other in others)


def test_no_user_chosen_name_survives_phase_one(canon: Canon, city: City, doc: Doc) -> None:
    written = doc(
        {"SecretCohortName": ["$SecretParameter", SEATS], "SecretOther": []},
        params={"SecretParameter": THAI},
        notes="SecretNotes",
        drafted_by="agent:SecretAgent",
    )
    written["cohorts"]["SecretCohortName"]["notes"] = "SecretCohortNotes"
    written["cohorts"]["SecretOther"]["all"] = [{"kind": "cohort", "cohort": "SecretCohortName"}]
    written["views"] = [{"analysis": "summary.distribution", "note": "SecretViewNote"}]
    result = canon(written, city(ROWS))
    assert result.refusals == []
    for cohort in result.cohorts.values():
        assert b"Secret" not in canonical(cohort.identity.hashed())
        assert b"Secret" not in canonical(list(cohort.clauses))
    assert result.cohorts["SecretOther"].id == result.cohorts["SecretCohortName"].id


@pytest.mark.parametrize(
    "clauses",
    [
        [THAI, SEATS],
        [{"not": value("establishments.tags", values=["vegan"], match="all")}],
        [{"any": [TEMP, {"kind": "covered", "table": "violations", "lift": "assessed"}]}],
        [{"kind": "ids", "ids": ["d:e1", {"dataset": "d", "key": ["e3"]}]}],
        [value("establishments.last_seen", range={"lt": "2024-01-01T10:00:00.5+02:00"})],
        [value("establishments.revenue", values=["9007199254740993", 1.5, 2.0])],
        [value("establishments.name", values=["$$abc", "$$$", "plain"])],
        [{"known": {"unknown": {"kind": "exists", "table": "complaints"}}}],
    ],
)
def test_canonicalising_a_canonical_form_changes_nothing(
    canon: Canon, city: City, doc: Doc, clauses: list[Any]
) -> None:
    release = city(ROWS)
    first = only(canon(doc(clauses), release))
    written = [as_document(clause, {release.manifest: "d"}) for clause in first.clauses]
    again = only(canon(doc(written), release))
    assert again.form == first.form
    assert again.id == first.id


def test_a_referenced_cohort_has_the_form_of_its_clauses_written_inline(
    canon: Canon, city: City, doc: Doc
) -> None:
    written = doc({"base": [THAI, SEATS], "rest": [{"kind": "cohort", "cohort": "base"}, TEMP]})
    inline = only(canon(doc([THAI, SEATS, TEMP]), city(ROWS)))
    referenced = canon(written, city(ROWS)).cohorts["rest"]
    assert referenced.form == inline.form
    assert referenced.id == inline.id


def test_the_id_hashes_the_form_unit_semantics_version_disclosure_and_packs(
    canon: Canon, city: City, doc: Doc
) -> None:
    release = city(ROWS)
    cohort = only(canon(doc([THAI]), release, floor=3))
    hashed_object = {
        "cohort": {release.manifest: THAI},
        "unit": "establishments",
        "semantics_version": SEMANTICS_VERSION,
        "disclosure": {"min_cell_count": 3},
        "packs": {},
    }
    assert cohort.identity.hashed() == hashed_object
    assert cohort.id == "drv:" + hashed(hashed_object)
    without = {key: member for key, member in hashed_object.items() if key != "disclosure"}
    assert cohort.computation_id == derivation_id(without)


def test_disclosure_changes_the_id_and_never_the_computation_id(
    canon: Canon, city: City, doc: Doc
) -> None:
    off = only(canon(doc([THAI]), city(ROWS)))
    floored = only(canon(doc([THAI]), city(ROWS), floor=5))
    dataset = city(ROWS, allow_row_ids=False)  # sets min_cell_count 5
    assert only(canon(doc([THAI]), dataset)).identity.disclosure == 5
    assert only(canon(doc([THAI]), dataset, floor=7)).identity.disclosure == 7
    assert only(canon(doc([THAI]), dataset, floor=2)).identity.disclosure == 5
    assert off.identity.disclosure is None
    assert off.id != floored.id
    assert off.computation_id == floored.computation_id


def test_leaf_keys_key_each_top_level_clause_and_the_leaves_as_written(
    canon: Canon, city: City, doc: Doc
) -> None:
    written = doc([SEATS, {"any": [THAI, THAI]}, {"kind": "exists", "table": "violations"}])
    cohort = only(canon(written, city(ROWS)))
    assert cohort.keys == tuple(leaf_key(clause) for clause in cohort.clauses)
    seats, thai, exists = cohort.keys
    assert cohort.leaves == {
        "/cohorts/c/all/0": (seats,),
        "/cohorts/c/all/1/any/0": (thai,),
        "/cohorts/c/all/1/any/1": (thai,),
        "/cohorts/c/all/2": (exists,),
    }
    population = count_parts(cohort, evaluate(cohort.resolved)).population
    assert set(population.unknown_by_leaf or {}) == set(cohort.keys)


def test_a_leaf_a_parameter_supplies_maps_from_the_parameter_reference(
    canon: Canon, city: City, doc: Doc
) -> None:
    written = doc(["$both"], params={"both": {"all": [THAI, SEATS]}})
    cohort = only(canon(written, city(ROWS)))
    assert cohort.leaves == {"/cohorts/c/all/0": tuple(sorted(cohort.keys))}


def test_a_leaf_that_became_no_clause_maps_to_none(canon: Canon, city: City, doc: Doc) -> None:
    written = doc({"every": [], "c": [{"kind": "cohort", "cohort": "every"}, THAI]})
    cohort = canon(written, city(ROWS)).cohorts["c"]
    assert cohort.leaves == {"/cohorts/c/all/1": cohort.keys}


def _predicates(written: Mapping[str, Any], *clauses: Any) -> list[ViewPredicate]:
    params = ExistenceParams.model_validate({"predicates": list(clauses)})
    return [
        ViewPredicate(
            f"0/{index}", written["dataset"], ("views", 0, "params", "predicates", index), clause
        )
        for index, clause in enumerate(params.predicates)
    ]


def test_a_view_s_predicate_is_canonicalised_as_the_cohort_of_its_one_clause(
    city: City, doc: Doc
) -> None:
    written = doc([THAI])
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    release = city(ROWS)
    result = canonicalise(
        loaded.document,
        {"d": release},
        labels={release.manifest: 1},
        predicates=_predicates(written, THAI, UNKNOWN_COLUMN),
    )
    predicate = result.predicates["0/0"]
    assert predicate.form == result.cohorts["c"].form
    assert predicate.id == result.cohorts["c"].id
    assert predicate.leaves == {"/views/0/params/predicates/0": predicate.keys}
    assert "0/1" not in result.predicates
    assert [(r.code, r.path) for r in result.refusals] == [
        (RefusalCode.UNKNOWN_COLUMN, "/views/0/params/predicates/1/column")
    ]


def test_the_release_is_recorded_with_its_label_and_status(
    canon: Canon, city: City, doc: Doc
) -> None:
    release = city(ROWS)
    draft = only(canon(doc([THAI]), release, labels={release.manifest: "draft"}))
    published = only(canon(doc([THAI]), release, labels={release.manifest: 4}))
    assert (draft.release.label, draft.release.status) == ("draft", "draft")
    assert (published.release.label, published.release.status) == (4, "published")
    assert draft.id == published.id


def _identity(tag: str, disclosure: int | None = None) -> CohortIdentity:
    return CohortIdentity({"sha256:" + "0" * 64: {"all": [tag]}}, "units", {}, disclosure)


def test_a_view_s_ids_hash_its_analysis_cohorts_params_packs_and_disclosure() -> None:
    cohorts = (_identity("a", 5), _identity("b", 5))
    view = ViewIdentity(
        "summary.distribution", "1.0.0", cohorts, {"columns": []}, {}, 5, reference=1
    )
    assert view.view() == {
        "analysis": {"id": "summary.distribution", "version": "1.0.0"},
        "cohorts": [cohorts[0].id, cohorts[1].id],
        "reference": 1,
        "params": {"columns": []},
    }
    assert view.id == derivation_id(
        {"view": view.view(), "disclosure": {"min_cell_count": 5}, "packs": {}}
    )
    unfloored = ViewIdentity(
        "summary.distribution",
        "1.0.0",
        (_identity("a"), _identity("b")),
        {"columns": []},
        {},
        None,
        reference=1,
    )
    assert view.id != unfloored.id
    assert view.computation_id == unfloored.computation_id
    assert view.hashed(disclosure=False)["view"]["cohorts"] == [  # type: ignore[index]
        cohorts[0].computation_id,
        cohorts[1].computation_id,
    ]


def test_a_view_that_names_no_cohorts_orders_them_by_ids_without_disclosure() -> None:
    cohorts = [_identity(tag, 3) for tag in "abcdef"]
    ordered = ViewIdentity.default_order(cohorts)
    assert [c.computation_id for c in ordered] == sorted(c.computation_id for c in cohorts)
    floored = [_identity(tag, 9) for tag in "abcdef"]
    assert [c.form for c in ViewIdentity.default_order(floored)] == [c.form for c in ordered]


def test_a_view_s_reference_is_one_of_its_positions() -> None:
    with pytest.raises(ValueError, match="reference"):
        ViewIdentity("summary.distribution", "1.0.0", (_identity("a"),), {}, {}, None, reference=1)


def test_the_hashed_objects_are_json_that_rfc_8785_writes() -> None:
    identity = _identity("a")
    assert json.loads(canonical(identity.hashed())) == identity.hashed()


def test_a_cohort_identity_s_ids_stay_as_built_whatever_is_done_to_its_parts() -> None:
    form: dict[str, Any] = {"sha256:" + "0" * 64: {"all": ["a"]}}
    packs = {"p": 1}
    identity = CohortIdentity(form, "units", packs, 5)
    built = (identity.id, identity.computation_id, identity.hashed())
    form["sha256:" + "0" * 64]["all"].append("b")
    packs["q"] = 2
    identity.hashed()["cohort"]["sha256:" + "0" * 64]["all"].append("c")  # type: ignore[index]
    identity.form["sha256:" + "0" * 64]["all"].append("d")  # type: ignore[index]
    assert (identity.id, identity.computation_id) == built[:2]
    assert identity.id == derivation_id(built[2])
    with pytest.raises(AttributeError):
        identity.unit = "other"  # type: ignore[misc]

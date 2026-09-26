"""Phase 2 of canonicalisation: views checked against their analyses, their canonical forms, ids
and readbacks (SPEC §7.4, §7.6, §7.7, §13.4; D317)."""

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from aibi.core.analyses import views
from aibi.core.analyses.registry import Analyses
from aibi.core.engine.canonical import Canonicalisation
from aibi.core.engine.ids import derivation_id
from aibi.core.schema.caveats import CaveatCode
from aibi.core.schema.loading import load_document
from aibi.core.schema.refusals import RefusalCode

Check = Callable[..., Any]
Shop = Callable[..., Any]

OLD = {"kind": "value", "column": "customers.age", "range": {"gte": 50}}
GOLD = {"kind": "value", "column": "customers.tier", "values": ["gold"]}
SILVER = {"kind": "value", "column": "customers.tier", "values": ["silver"]}
WEB = {
    "kind": "exists",
    "table": "orders",
    "where": [{"kind": "value", "column": "orders.channel", "values": ["web"]}],
}


def document(view: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    given = view or {
        "analysis": "compare.existence",
        "cohorts": ["gold", "silver"],
        "params": {"predicates": [OLD]},
    }
    return {
        "aibi": "1",
        "dataset": "d",
        "unit": "customers",
        "cohorts": {"gold": {"all": [GOLD]}, "silver": {"all": [SILVER]}},
        "views": [given],
        **extra,
    }


def refusals(found: Any) -> list[tuple[str, str | None]]:
    return [(refusal.code, refusal.path) for refusal in found.refusals]


def test_a_view_that_checks_has_its_canonical_form_and_ids(check: Check, shop: Shop) -> None:
    found = check(document(), shop())
    assert found.refusals == []
    [view] = found.views
    gold, silver = found.canonical.cohorts["gold"], found.canonical.cohorts["silver"]
    form = view.identity.view()
    assert form == {
        "analysis": {"id": "compare.existence", "version": "1.0.0"},
        "cohorts": [gold.id, silver.id],
        "params": {"level": 0.95, "predicates": [view.predicates[0].form[gold.release.manifest]]},
        "reference": 0,
        "overlap": False,
    }
    assert view.identity.id == derivation_id(
        {"view": form, "packs": {}, "disclosure": {"min_cell_count": None}}
    )


def test_no_name_order_or_default_changes_a_view_s_id(check: Check, shop: Shop) -> None:
    first = check(document(), shop()).views[0].identity.id
    renamed = document(
        {"analysis": "compare.existence", "cohorts": ["g", "s"], "params": {"predicates": [OLD]}}
    )
    renamed["cohorts"] = {"s": {"all": [SILVER]}, "g": {"all": [GOLD]}}
    explicit = document(
        {
            "params": {"level": 0.95, "predicates": [OLD]},
            "cohorts": ["gold", "silver"],
            "analysis": "compare.existence",
            "note": "a note",
        }
    )
    for written in (renamed, explicit):
        assert check(written, shop()).views[0].identity.id == first


def test_the_reference_and_the_overlap_setting_change_a_view_s_id(check: Check, shop: Shop) -> None:
    first = check(document(), shop()).views[0].identity
    swapped = document(
        {
            "analysis": "compare.existence",
            "cohorts": ["gold", "silver"],
            "reference": "silver",
            "params": {"predicates": [OLD]},
        }
    )
    overlap = document(
        {
            "analysis": "compare.existence",
            "cohorts": ["gold", "silver"],
            "overlap": "allow",
            "params": {"predicates": [OLD]},
        }
    )
    found = {first.id, *(check(w, shop()).views[0].identity.id for w in (swapped, overlap))}
    assert len(found) == 3
    assert check(swapped, shop()).views[0].reference == 1


def test_an_unknown_analysis_is_refused_listing_the_registry(check: Check, shop: Shop) -> None:
    found = check(document({"analysis": "compare.nothing", "cohorts": ["gold"]}), shop())
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (RefusalCode.UNKNOWN_ANALYSIS, "/views/0/analysis")
    assert [segment.model_dump() for segment in refusal.alternatives] == [
        {"data": "compare.existence"},
        {"data": "summary.distribution"},
    ]
    assert found.views == []


def test_a_core_analysis_of_a_later_slice_is_not_supported_naming_it(
    check: Check, shop: Shop
) -> None:
    for analysis, slice_ in (
        ("summary.members", "M3.2b"),
        ("compare.columns", "M3.2c"),
        ("survival.km", "M3.3"),
    ):
        found = check(document({"analysis": analysis, "cohorts": ["gold"]}), shop())
        [refusal] = found.refusals
        assert (refusal.code, refusal.path) == (RefusalCode.NOT_SUPPORTED, "/views/0/analysis")
        assert {"text": f" (§9.5) is run from {slice_}; this server runs "} in [
            segment.model_dump() for segment in refusal.message
        ]
        assert found.views == []


def test_parameters_are_refused_where_they_are_written(check: Check, shop: Shop) -> None:
    def view(params: Any) -> dict[str, Any]:
        return {"analysis": "compare.existence", "cohorts": ["gold"], "params": params}

    assert refusals(check(document(view({})), shop())) == [
        (RefusalCode.MISSING_MEMBER, "/views/0/params/predicates")
    ]
    assert refusals(check(document(view({"predicates": [OLD], "tails": 2})), shop())) == [
        (RefusalCode.UNKNOWN_MEMBER, "/views/0/params/tails")
    ]
    for level in (1.5, 1 - 2**-53, 1 - 1e-10):
        assert refusals(check(document(view({"predicates": [OLD], "level": level})), shop())) == [
            (RefusalCode.INVALID_VALUE, "/views/0/params/level")
        ]
    assert refusals(check(document(view({"predicates": [OLD], "level": 1 - 1e-9})), shop())) == []
    [refusal] = check(document(view({"predicates": [{"kind": "nope"}]})), shop()).refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.UNKNOWN_KIND,
        "/views/0/params/predicates/0",
    )
    assert "value" in [segment.model_dump().get("text") for segment in refusal.alternatives]
    assert refusals(check(document(view({"predicates": []})), shop())) == [
        (RefusalCode.INVALID_VALUE, "/views/0/params/predicates")
    ]


def test_a_predicate_given_as_a_parameter_is_refused_at_its_reference(
    check: Check, shop: Shop
) -> None:
    written = document(
        {"analysis": "compare.existence", "cohorts": ["gold"], "params": {"predicates": "$p"}},
        params={"p": [{"kind": "value", "column": "customers.height", "values": [1]}]},
    )
    [refusal] = check(written, shop()).refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.UNKNOWN_COLUMN,
        "/views/0/params/predicates",
    )
    assert {"data": "p"} in [segment.model_dump() for segment in refusal.message]


def test_an_analysis_with_a_reference_needs_its_cohorts_listed(check: Check, shop: Shop) -> None:
    found = check(
        document({"analysis": "compare.existence", "params": {"predicates": [OLD]}}), shop()
    )
    assert refusals(found) == [(RefusalCode.MISSING_MEMBER, "/views/0/cohorts")]


def test_a_predicate_holds_no_ids_and_no_cohort_leaf(check: Check, shop: Shop) -> None:
    predicates = [
        {"any": [OLD, {"kind": "ids", "ids": ["d:c1"]}]},
        {"kind": "cohort", "cohort": "gold"},
    ]
    written = document(
        {"analysis": "compare.existence", "cohorts": ["gold"], "params": {"predicates": predicates}}
    )
    assert refusals(check(written, shop())) == [
        (RefusalCode.LEAF_NOT_ALLOWED, "/views/0/params/predicates/0/any/1"),
        (RefusalCode.LEAF_NOT_ALLOWED, "/views/0/params/predicates/1"),
    ]


def test_a_predicate_is_resolved_on_the_unit_table_and_refused_where_it_is_written(
    check: Check, shop: Shop
) -> None:
    written = document(
        {
            "analysis": "compare.existence",
            "cohorts": ["gold"],
            "params": {
                "predicates": [OLD, {"kind": "value", "column": "customers.height", "values": [1]}]
            },
        }
    )
    found = check(written, shop())
    assert refusals(found) == [(RefusalCode.UNKNOWN_COLUMN, "/views/0/params/predicates/1/column")]
    assert found.views == []


def test_a_view_s_readback_names_its_cohorts_by_position_and_states_each_predicate(
    check: Check, shop: Shop
) -> None:
    [view] = check(document(), shop()).views
    segments = [segment.model_dump() for segment in view.readback()]
    words = "".join(segment.get("text", "") for segment in segments)
    assert "gold" not in words
    assert "silver" not in words
    assert "the reference being the cohort at position 0" in words
    assert "Predicate 0: units for which " in words
    assert {"data": "0.95"} in segments


def test_a_view_s_static_caveats_say_what_its_predicates_read_unconfirmed(
    check: Check, shop: Shop
) -> None:
    release = shop()
    [view] = check(
        document(
            {"analysis": "compare.existence", "cohorts": ["gold"], "params": {"predicates": [WEB]}}
        ),
        release,
        label="draft",
    ).views
    caveats = {(caveat.code, tuple(caveat.affects)) for caveat in view.static_caveats()}
    assert (CaveatCode.DRAFT_RELEASE, ("/derivation/releases",)) in caveats
    assert view.release.status == "draft"


def test_the_effective_k_of_a_view_is_its_cohorts_and_the_floor(check: Check, shop: Shop) -> None:
    [view] = check(document(), shop(disclosure={"min_cell_count": 3}), floor=5).views
    assert view.disclosure == 5
    [view] = check(document(), shop(disclosure={"min_cell_count": 7}), floor=5).views
    assert view.disclosure == 7


def test_a_view_whose_cohorts_are_of_two_releases_is_refused_by_phase_2_itself(
    check: Check, shop: Shop
) -> None:
    """Resolution refuses cohorts of two releases of one dataset first, and a concept unit, the
    one way a view's cohorts could span datasets, is not supported yet; ``checked`` refuses such
    a view on its own, at its cohorts, rather than leaving it out (D317)."""
    written = document()
    one = check(written, shop())
    other = check(written, replace(shop(), manifest="sha256:" + "1" * 64))
    gold, silver = one.canonical.cohorts["gold"], other.canonical.cohorts["silver"]
    assert gold.release.manifest != silver.release.manifest
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    parsed, refused = views.parse(loaded.document, loaded.positions, Analyses())
    assert refused == []
    mixed = Canonicalisation({"gold": gold, "silver": silver}, [], one.canonical.predicates)
    found, refusals = views.checked(loaded.document, parsed, mixed)
    assert found == []
    assert [(refusal.code, refusal.path) for refusal in refusals] == [
        (RefusalCode.MIXED_RELEASES, "/views/0/cohorts")
    ]

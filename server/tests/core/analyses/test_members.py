"""``summary.members``: a page of one cohort's unit keys, its phase 2 and its refusals (SPEC §7.6,
§8.1, §8.4, §9.5; D331, D332), over the shop and over things keyed as the tests need, run by the
reference evaluator."""

import json
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from pydantic import ValidationError

from aibi.core.analyses import members
from aibi.core.analyses.existence import CohortAt
from aibi.core.engine import build
from aibi.core.engine.counts import count_parts
from aibi.core.engine.data import Release
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.members import keys, ordered, written
from aibi.core.engine.suppression import disclosed
from aibi.core.schema.analyses import MembersParams, MembersPosition
from aibi.core.schema.caveats import CaveatCode
from aibi.core.schema.output import Data
from aibi.core.schema.refusals import RefusalCode
from aibi.core.schema.semantics import ExclusionReason

Analyse = Callable[..., list[Any]]
Check = Callable[..., Any]
Shop = Callable[..., Any]
Rows = Callable[..., dict[str, list[dict[str, object]]]]

OLD = {"kind": "value", "column": "customers.age", "range": {"gte": 50}}
YES = {"kind": "value", "column": "things.flag", "values": ["yes"]}


def document(given: dict[str, Sequence[Any]] | None = None, **view: Any) -> dict[str, Any]:
    given = given if given is not None else {"old": [OLD]}
    return {
        "aibi": "1",
        "dataset": "d",
        "unit": "customers",
        "cohorts": {name: {"all": list(clauses)} for name, clauses in given.items()},
        "views": [{"analysis": "summary.members", **view}],
    }


def things(
    flags: Sequence[str | None], *, datatype: str | None = "string", **dataset: Any
) -> Release:
    """Things keyed by ``key-<n>``, each ``yes``, ``no`` or empty (UNKNOWN) in ``flag``."""
    return build.release(
        [
            build.dataset(**dataset),
            build.table("things", ["thing_id"]),
            build.column("things.thing_id", datatype),
            build.column(
                "things.flag",
                "category",
                permissible_values={"values": [{"value": "yes"}, {"value": "no"}]},
            ),
        ],
        {
            "things": [
                {"thing_id": f"key-{index:03d}", "flag": flag} for index, flag in enumerate(flags)
            ]
        },
    )


def things_document(given: dict[str, Sequence[Any]], **view: Any) -> dict[str, Any]:
    return {**document(given, **view), "unit": "things"}


def old_customers(rows: Rows) -> list[str]:
    """The shop's customers of 50 or more, in the canonical form's order, found independently:
    each key's JSON text compared as UTF-16 code units."""
    found = [row["customer_id"] for row in rows()["customers"] if int(str(row["age"])) >= 50]
    return sorted(
        (str(key) for key in found),
        key=lambda key: json.dumps([key], ensure_ascii=False).encode("utf-16-be"),
    )


def listed(result: Any) -> list[list[Any]]:
    [position] = result.values.model_dump(mode="json")["positions"]
    return position["keys"]


def test_a_page_lists_the_cohort_s_members_keys_in_the_canonical_form_s_order(
    analyse: Analyse, shop: Shop, rows: Rows
) -> None:
    [found] = analyse(document(cohorts=["old"], params={"offset": 2, "limit": 5}), shop())
    expected = old_customers(rows)
    assert listed(found.result) == [[{"data": key}] for key in expected[2:7]]
    [position] = found.result.values.model_dump(mode="json")["positions"]
    assert position["columns"] == [{"data": "customers.customer_id"}]
    assert (position["offset"], position["more"]) == (2, True)
    assert found.result.population[0].n_true == len(expected)


def test_the_pages_of_every_offset_and_limit_give_each_member_once_in_order(
    analyse: Analyse, shop: Shop, rows: Rows
) -> None:
    expected = [[{"data": key}] for key in old_customers(rows)]
    for limit in (1, 2, 3, 5, len(expected), len(expected) + 1):
        pages: list[list[Any]] = []
        offset, more = 0, True
        while more:
            [found] = analyse(
                document(cohorts=["old"], params={"offset": offset, "limit": limit}), shop()
            )
            [position] = found.result.values.model_dump(mode="json")["positions"]
            assert len(position["keys"]) <= limit
            pages += position["keys"]
            offset, more = offset + limit, position["more"]
        assert pages == expected
    [beyond] = analyse(document(cohorts=["old"], params={"offset": 1000}), shop())
    assert listed(beyond.result) == []


def test_the_result_counts_the_members_excludes_none_and_draws_no_chart(
    analyse: Analyse, shop: Shop
) -> None:
    [found] = analyse(document(cohorts=["old"]), shop())
    result = found.result
    [analysed] = result.analysed
    n_true = result.population[0].n_true
    assert (analysed.n, analysed.excluded_units) == (n_true, 0)
    assert analysed.excluded == dict.fromkeys(ExclusionReason, 0)
    assert result.charts == []
    assert result.values.view == {}
    assert result.derivation.analysis.id == "summary.members"
    assert result.derivation.disclosure.min_cell_count is None
    assert CaveatCode.SUPPRESSED not in {caveat.code for caveat in result.caveats}


def test_a_view_lists_the_keys_of_exactly_one_cohort(check: Check, shop: Shop) -> None:
    two = {"old": [OLD], "all": []}
    found = check(document(two, cohorts=["old", "all"]), shop())
    assert [(r.code, r.path) for r in found.refusals] == [
        (RefusalCode.INVALID_VALUE, "/views/0/cohorts")
    ]
    found = check(document(two), shop())
    assert [(r.code, r.path) for r in found.refusals] == [
        (RefusalCode.MISSING_MEMBER, "/views/0/cohorts")
    ]
    assert found.views == []
    found = check(document(), shop())
    assert found.refusals == []
    [view] = found.views
    assert [cohort.name for cohort in view.cohorts] == ["old"]


@pytest.mark.parametrize(
    ("params", "path"),
    [
        ({"limit": 0}, "/views/0/params/limit"),
        ({"limit": 1001}, "/views/0/params/limit"),
        ({"offset": -1}, "/views/0/params/offset"),
        ({"offset": 1.5}, "/views/0/params/offset"),
        ({"page": 2}, "/views/0/params/page"),
    ],
)
def test_a_page_s_parameters_are_refused_where_they_are_written(
    check: Check, shop: Shop, params: dict[str, Any], path: str
) -> None:
    found = check(document(params=params), shop())
    [refusal] = found.refusals
    assert refusal.path == path
    assert found.views == []


def test_the_canonical_parameters_write_every_default(check: Check, shop: Shop) -> None:
    [plain] = check(document(), shop()).views
    [spelled] = check(document(params={"offset": 0, "limit": 100}), shop()).views
    [later] = check(document(params={"offset": 100}), shop()).views
    assert plain.identity.view()["params"] == {"limit": 100, "offset": 0}
    assert plain.identity.id == spelled.identity.id
    assert later.identity.id != plain.identity.id


def test_the_readback_states_the_key_columns_the_order_and_the_page_as_data(
    check: Check, shop: Shop
) -> None:
    [view] = check(document(params={"offset": 3, "limit": 7}), shop()).views
    read = [segment.model_dump(exclude_none=True) for segment in view.readback()]
    tokens = [segment["data"] for segment in read if "data" in segment]
    assert tokens == ["customers.customer_id", "3", "7"]
    assert "old" not in json.dumps(read)


def test_keys_are_carried_as_the_canonical_form_writes_them(
    analyse: Analyse,
) -> None:
    release = build.release(
        [
            build.dataset(),
            build.table("things", ["big", "when", "size", "odd"]),
            build.column("things.big", "integer"),
            build.column("things.when", "date"),
            build.column("things.size", "number"),
            build.column("things.odd", "boolean"),
        ],
        {"things": [{"big": 2**60, "when": "2024-02-29", "size": 3.0, "odd": True}]},
    )
    written_document = {**document({"every": []}), "unit": "things"}
    [found] = analyse(written_document, release)
    [position] = found.result.values.model_dump(mode="json")["positions"]
    assert position["keys"] == [[str(2**60), {"data": "2024-02-29"}, 3, True]]
    assert [column["data"] for column in position["columns"]] == [
        "things.big",
        "things.when",
        "things.size",
        "things.odd",
    ]


def test_a_key_column_whose_datatype_nobody_declared_is_read_as_text_and_unconfirmed(
    analyse: Analyse,
) -> None:
    release = things(["yes", "no"], datatype=None)
    [found] = analyse(things_document({"yes": [YES]}), release)
    assert listed(found.result) == [[{"data": "key-000"}]]
    [unconfirmed] = [
        caveat for caveat in found.result.caveats if caveat.code == CaveatCode.UNCONFIRMED_SEMANTICS
    ]
    said = json.dumps([segment.model_dump() for segment in unconfirmed.message])
    assert "things.thing_id/fields/datatype" in said
    assert "undeclared" in said


def test_list_members_refuses_a_disclosure_setting(check: Check, shop: Shop) -> None:
    [view] = check(document(), shop()).views
    [cohort] = view.cohorts
    position = CohortAt(cohort, evaluate(cohort.resolved))
    with pytest.raises(ValueError, match="D332"):
        members.list_members(position, ordered(keys(cohort.resolved)), MembersParams(), k=3)


@pytest.mark.parametrize(
    ("dataset", "floor"),
    [
        ({"disclosure": {"min_cell_count": 5}}, None),
        ({}, 2),
        ({"disclosure": {"min_cell_count": 3, "allow_row_ids": False}}, None),
    ],
)
def test_a_view_is_refused_under_any_disclosure_setting_whatever_its_cohort_s_size(
    check: Check, dataset: dict[str, Any], floor: int | None
) -> None:
    release = things(["yes"] * 40, **dataset)
    found = check(things_document({"yes": [YES]}), release, floor=floor)
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.ROW_IDS_NOT_ALLOWED,
        "/views/0/analysis",
    )
    assert found.views == []
    assert "key-" not in refusal.model_dump_json()
    said = json.dumps([segment.model_dump() for segment in refusal.message])
    assert ("row ids" in said) == (dataset.get("disclosure", {}).get("allow_row_ids") is False)


def test_lists_of_cohorts_whose_sizes_are_shown_would_name_the_units_a_count_s_pass_hides(
    check: Check,
) -> None:
    """Why no list is given under *k* (D332): over 24 units under *k* = 4, ``X`` of 8, 13 and 3
    units shows only its 13, but every unit, ``not X`` and ``X and known X`` each show their
    size, of *k* units or more, and their keys by difference name the 3 units whose ``X`` is
    unknown."""
    flags = ["yes"] * 8 + ["no"] * 13 + [None] * 3
    release = things(flags)
    written_document = things_document(
        {
            "x": [YES],
            "every": [],
            "not_x": [{"not": YES}],
            "x_and_known_x": [YES, {"known": YES}],
        },
        cohorts=["x"],
    )
    checked = check(written_document, release, floor=4)
    shown: dict[str, Any] = {}
    for name, cohort in checked.canonical.cohorts.items():
        counted = disclosed(count_parts(cohort, evaluate(cohort.resolved)), 4)
        shown[name] = counted.population
    assert shown["x"].n_true is None
    assert [shown[name].n_true for name in ("every", "not_x", "x_and_known_x")] == [24, 13, 8]
    listed_by: dict[str, set[str]] = {
        name: {str(key[0]) for key in keys(checked.canonical.cohorts[name].resolved)}
        for name in ("every", "not_x", "x_and_known_x")
    }
    named = listed_by["every"] - listed_by["not_x"] - listed_by["x_and_known_x"]
    assert sorted(named) == ["key-021", "key-022", "key-023"]
    assert [refusal.code for refusal in checked.refusals] == [RefusalCode.ROW_IDS_NOT_ALLOWED]


def test_a_members_key_is_written_as_an_ids_leaf_s_member_is(check: Check) -> None:
    release = things(["yes", "no", "yes"])
    [view] = check(things_document({"yes": [YES]}), release).views
    found = [written(key) for key in ordered(keys(view.cohorts[0].resolved))]
    ids = {
        "kind": "ids",
        "ids": [{"dataset": "d", "key": key} for key in found],
    }
    again = check(things_document({"listed": [ids]}), release)
    assert again.refusals == []
    [cohort] = again.canonical.cohorts.values()
    assert [written(key) for key in ordered(keys(cohort.resolved))] == found


def test_a_key_s_decimal_string_holds_an_integer_beyond_the_safe_range_alone() -> None:
    columns = [Data(data="things.big")]
    MembersPosition(columns=columns, keys=[[str(2**53)]], offset=0, more=False)
    for wrong in ("12", "-9007199254740991", "1e20", "0x20000000000001"):
        with pytest.raises(ValidationError):
            MembersPosition(columns=columns, keys=[[wrong]], offset=0, more=False)
    with pytest.raises(ValidationError):
        MembersPosition(columns=columns, keys=[[1, 2]], offset=0, more=False)


def test_a_key_s_fields_not_confirmed_raise_unconfirmed_semantics(analyse: Analyse) -> None:
    release = build.release(
        [
            build.dataset(),
            build.descriptor(
                "table",
                "things",
                {"role": "entity", "primary_key": ["thing_id"]},
                statuses={"primary_key": "imported_default"},
            ),
            build.column("things.thing_id", "string", status="proposed"),
        ],
        {"things": [{"thing_id": "key-000"}]},
    )
    [found] = analyse({**document({"every": []}), "unit": "things"}, release)
    [unconfirmed] = [
        caveat
        for caveat in found.result.caveats
        if caveat.code == CaveatCode.UNCONFIRMED_SEMANTICS and caveat.affects == ["/values"]
    ]
    said = json.dumps([segment.model_dump() for segment in unconfirmed.message])
    assert "things/fields/primary_key" in said
    assert "things.thing_id/fields/datatype" in said
    assert "proposed" in said
    assert "imported_default" in said

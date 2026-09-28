"""``survival.cox``: its parameters, phase 2, coding of each kind of variable, members' cells,
values, run-time refusals, caveats, readback, ids and chart (SPEC §7.6, §8.1–§8.5, §9.1, §9.5;
D362–D369), over the shop evaluated by the reference evaluator and over members given as they
are."""

import json
import math
import random
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest

from aibi.core.analyses import cox, survival, timetoevent
from aibi.core.analyses.charts import cox_chart
from aibi.core.analyses.cox import Covariate, Member
from aibi.core.analyses.existence import CohortAt
from aibi.core.engine import build
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.inputs import Listed, listed, ordered
from aibi.core.engine.truth import Mark
from aibi.core.engine.worker import CallerDeadline
from aibi.core.schema.analyses import CoxParams, CoxTerm, CoxValues
from aibi.core.schema.digests import order_key
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.limits import MAX_TEXT
from aibi.core.schema.loading import refusal_as_written
from aibi.core.schema.refusals import Refusal, RefusalCode
from aibi.core.schema.semantics import ExclusionReason, Flag

Analyse = Callable[..., list[Any]]
Check = Callable[..., Any]
Shop = Callable[..., Any]
Rows = Callable[..., dict[str, list[dict[str, object]]]]

ORIGIN = timetoevent.ORIGIN
OLD = {"kind": "value", "column": "customers.age", "range": {"gte": 50}}
YOUNG = {"kind": "value", "column": "customers.age", "range": {"lt": 50}}
EVERYONE = {"kind": "value", "column": "customers.age", "range": {"gte": 0}}
TIER = {"column": "customers.tier"}
AGE = {"column": "customers.age"}
NOT_ASSESSED = frozenset({ExclusionReason.NOT_ASSESSED})
NONE = frozenset[ExclusionReason]()

SIZES = ("small", "medium", "large")
PRIORITIES = ("low", "high", "urgent")


def document(
    cohorts: Mapping[str, Sequence[Any]] | None = None,
    params: Mapping[str, Any] | None = None,
    **view: Any,
) -> dict[str, Any]:
    given = cohorts if cohorts is not None else {"young": [YOUNG], "old": [OLD]}
    return {
        "aibi": "1",
        "dataset": "d",
        "unit": "customers",
        "cohorts": {name: {"all": list(clauses)} for name, clauses in given.items()},
        "views": [
            {
                "analysis": "survival.cox",
                "cohorts": list(given),
                "params": dict(params or {}),
                **view,
            }
        ],
    }


def extras() -> list[Any]:
    """The columns the kinds' tests read beside the shop's: an ordered category, a string, a
    boolean, a date and a number on the customers, and an ordered category on their orders."""
    ordered = {"values": [{"value": v} for v in SIZES], "ordered": True}
    return [
        build.column("customers.size", "category", permissible_values=ordered),
        build.column("customers.note", "string"),
        build.column("customers.vip", "boolean"),
        build.column("customers.since", "date"),
        build.column("customers.score", "number"),
        build.column(
            "orders.priority",
            "category",
            permissible_values={"values": [{"value": v} for v in PRIORITIES], "ordered": True},
        ),
    ]


def rows_with_extras(rows: Rows, customers: int = 24) -> dict[str, list[dict[str, object]]]:
    """The shop's rows with the extras' values: sizes, notes, a VIP flag and scores of every
    customer and priorities of every order."""
    found = rows(customers, extended=True, survived=True)
    for n, customer in enumerate(found["customers"], 1):
        customer["size"] = SIZES[n % 3]
        customer["note"] = ("plain", "fussy", "kind")[n % 3]
        customer["vip"] = n % 4 == 0
        customer["since"] = f"2020-01-{(n % 28) + 1:02d}"
        customer["score"] = float(n % 7)
    for n, order in enumerate(found["orders"], 1):
        order["priority"] = PRIORITIES[n % 3]
    return found


@pytest.fixture
def store(shop: Shop, rows: Rows) -> Callable[..., Any]:
    """The shop with retention, amounts and the extras, its rows edited by ``edit`` if given."""

    def made(
        *,
        customers: int = 24,
        entry: str = "delayed",
        edit: Callable[[dict[str, list[dict[str, object]]]], None] | None = None,
        **options: Any,
    ) -> Any:
        given = rows_with_extras(rows, customers)
        if edit is not None:
            edit(given)
        return shop(given, extended=True, survived=entry, extras=extras(), **options)

    return made


Store = Callable[..., Any]


def refusals(found: Any) -> list[tuple[RefusalCode, str | None]]:
    return [(refusal.code, refusal.path) for refusal in found.refusals]


def codes(found: Any) -> set[str]:
    return {caveat.code for caveat in found.caveats}


def said(parts: Sequence[Any]) -> str:
    return "".join(getattr(part, "text", None) or getattr(part, "data", "") for part in parts)


# --- Parameters and phase 2 (D366, D367) ---------------------------------------------------------


def test_the_parameters_take_variables_as_covariates_and_a_stratum() -> None:
    params = CoxParams.model_validate({"covariates": [TIER, AGE], "stratum": {"column": "c.s"}})
    assert [covariate.column for covariate in params.covariates] == [
        "customers.tier",
        "customers.age",
    ]
    assert params.stratum is not None
    assert (params.endpoint, params.level) == (None, 0.95)
    assert CoxParams.model_validate({}).covariates == []


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"covariates": [TIER] * 9}, (RefusalCode.LIMIT_EXCEEDED, "/views/0/params/covariates")),
        (
            {"covariates": [{**TIER, "kind": "column"}]},
            (RefusalCode.UNKNOWN_MEMBER, "/views/0/params/covariates/0/kind"),
        ),
        ({"covariates": [TIER], "level": 1}, (RefusalCode.INVALID_VALUE, "/views/0/params/level")),
        (
            {"covariates": [TIER], "level": 0.9999999999},
            (RefusalCode.INVALID_VALUE, "/views/0/params/level"),
        ),
        (
            {"covariates": [TIER], "ties": "breslow"},
            (RefusalCode.UNKNOWN_MEMBER, "/views/0/params/ties"),
        ),
        ({"covariates": [{}]}, (RefusalCode.MISSING_MEMBER, "/views/0/params/covariates/0/column")),
    ],
    ids=["nine covariates", "a tag", "level 1", "level over its cap", "ties", "no column"],
)
def test_parameters_it_does_not_take_are_refused_where_they_are_written(
    check: Check, store: Store, params: dict[str, Any], expected: tuple[RefusalCode, str]
) -> None:
    assert refusals(check(document(params=params), store())) == [expected]


@pytest.mark.parametrize(
    "params",
    [{}, {"stratum": TIER}],
    ids=["nothing", "a stratum alone"],
)
def test_a_model_of_one_cohort_without_covariates_is_refused_as_it_has_no_term(
    check: Check, store: Store, params: dict[str, Any]
) -> None:
    found = check(document({"all": [EVERYONE]}, params), store())
    assert refusals(found) == [(RefusalCode.MISSING_MEMBER, "/views/0/params/covariates")]
    assert check(document({"all": [EVERYONE]}, {"covariates": [AGE]}), store()).refusals == []
    assert check(document(params=params), store()).refusals == []


@pytest.mark.parametrize(
    ("variable", "expected"),
    [
        ({"column": "customers.since"}, (RefusalCode.NOT_SUPPORTED, "column")),
        ({"column": "customers.customer_id"}, (RefusalCode.INVALID_VALUE, "column")),
        ({"column": "customers.tenure"}, (RefusalCode.INVALID_VALUE, "column")),
        ({"column": "customers.left"}, (RefusalCode.INVALID_VALUE, "column")),
        ({"column": "customers.age", "bins": [0, 50, 100]}, (RefusalCode.INVALID_VALUE, "bins")),
        (
            {"column": "orders.order_id", "aggregate": "count", "count": "rows"},
            (RefusalCode.INVALID_VALUE, "count"),
        ),
        (
            {
                "column": "orders.amount",
                "aggregate": "mean",
                "where": [{"kind": "cohort", "cohort": "young"}],
            },
            (RefusalCode.LEAF_NOT_ALLOWED, "where/0"),
        ),
    ],
    ids=["a date", "an identifier", "the time", "the status", "bins", "rows", "a cohort leaf"],
)
@pytest.mark.parametrize("place", ["covariates/0", "stratum"])
def test_a_covariate_or_stratum_it_cannot_model_is_refused_where_it_is_written(
    check: Check,
    store: Store,
    variable: dict[str, Any],
    expected: tuple[RefusalCode, str],
    place: str,
) -> None:
    params = (
        {"covariates": [variable]}
        if place != "stratum"
        else {"covariates": [AGE], "stratum": variable}
    )
    found = check(document(params=params), store())
    code, member = expected
    assert refusals(found) == [(code, f"/views/0/params/{place}/{member}")]
    assert found.views == []
    if code == RefusalCode.NOT_SUPPORTED:
        [refusal] = found.refusals
        assert [segment.model_dump() for segment in refusal.alternatives] == [
            {"data": name} for name in cox.TAKEN
        ]


def test_the_count_of_an_identifier_s_rows_is_a_number_covariate(
    check: Check, store: Store
) -> None:
    variable = {"column": "orders.order_id", "aggregate": "count"}
    [view] = check(document(params={"covariates": [variable]}), store()).views
    assert cox.covariate_of(view.variables[0].resolved) == Covariate("number")


def test_under_a_disclosure_setting_the_view_is_withheld_before_anything_else(
    check: Check, store: Store
) -> None:
    params = {"covariates": [{"column": "customers.since"}]}
    for found in (
        check(document(params=params), store(), floor=3),
        check(document(params=params), store(disclosure={"min_cell_count": 5})),
    ):
        assert refusals(found) == [(RefusalCode.WITHHELD_UNDER_K, "/views/0/analysis")]


KINDS: list[tuple[dict[str, Any], Covariate]] = [
    (TIER, Covariate("category")),
    ({"column": "customers.size"}, Covariate("category", SIZES)),
    ({"column": "customers.note"}, Covariate("category")),
    ({"column": "customers.vip"}, Covariate("boolean")),
    (AGE, Covariate("number")),
    ({"column": "customers.joined"}, Covariate("number")),
    ({"column": "customers.score"}, Covariate("number")),
    ({"column": "orders.channel", "aggregate": "some", "values": ["web"]}, Covariate("boolean")),
    ({"column": "orders.channel", "aggregate": "every", "values": ["web"]}, Covariate("boolean")),
    ({"column": "orders.order_id", "aggregate": "count"}, Covariate("number")),
    ({"column": "orders.amount", "aggregate": "mean"}, Covariate("number")),
    ({"column": "orders.amount", "aggregate": "max"}, Covariate("number")),
    ({"column": "orders.amount", "aggregate": "min"}, Covariate("number")),
    ({"column": "orders.priority", "aggregate": "max"}, Covariate("category", PRIORITIES)),
    ({"column": "orders.priority", "aggregate": "min"}, Covariate("category", PRIORITIES)),
]


@pytest.mark.parametrize(("variable", "expected"), KINDS, ids=[str(v) for v, _ in KINDS])
def test_each_kind_of_variable_codes_as_d367_says(
    check: Check, store: Store, variable: dict[str, Any], expected: Covariate
) -> None:
    [view] = check(document(params={"covariates": [variable]}), store()).views
    assert cox.covariate_of(view.variables[0].resolved) == expected


def test_the_canonical_parameters_hold_every_default_and_the_variables_forms(
    check: Check, store: Store
) -> None:
    [view] = check(document(params={"covariates": [TIER, AGE]}), store()).views
    assert view.identity.params == {
        "covariates": [{"column": "customers.tier"}, {"column": "customers.age"}],
        "endpoint": {"id": "ep:retention", "time": "customers.tenure"},
        "level": 0.95,
        "stratum": None,
    }
    [stratified] = check(
        document(params={"covariates": [AGE], "stratum": TIER, "level": 0.9}), store()
    ).views
    assert stratified.identity.params == {
        "covariates": [{"column": "customers.age"}],
        "endpoint": {"id": "ep:retention", "time": "customers.tenure"},
        "level": 0.9,
        "stratum": {"column": "customers.tier"},
    }


def test_the_id_depends_on_the_covariates_order_but_not_on_names_or_key_order(
    check: Check, store: Store
) -> None:
    def identity(written: dict[str, Any]) -> str:
        [view] = check(written, store()).views
        return view.identity.id

    first = identity(document(params={"covariates": [TIER, AGE]}))
    renamed = document({"under": [YOUNG], "over": [OLD]}, {"covariates": [TIER, AGE]})
    reordered = json.loads(json.dumps(document(params={"level": 0.95, "covariates": [TIER, AGE]})))
    assert identity(renamed) == first
    assert identity(reordered) == first
    assert identity(document(params={"covariates": [AGE, TIER]})) != first
    assert identity(document(params={"covariates": [AGE], "stratum": TIER})) != identity(
        document(params={"covariates": [AGE, TIER]})
    )


# --- Values (D363–D365, D368) ------------------------------------------------------------------


def keyed(row: Mapping[str, object]) -> bytes:
    return json.dumps([row["customer_id"]], ensure_ascii=False).encode("utf-16-be")


def members_from_rows(
    rows: Mapping[str, Sequence[Mapping[str, object]]], cohort: Callable[[int], bool]
) -> list[Member]:
    """A cohort's members' cells found from the shop's rows alone, in the order of their keys:
    retention with delayed entry, then the tier (``?`` not assessed) and the age."""
    found: list[Member] = []
    for row in sorted(
        (row for row in rows["customers"] if cohort(int(str(row["age"])))), key=keyed
    ):
        left = row["left"]
        endpoint = None if left == "?" else (row["joined"], row["tenure"], left == "yes")
        tier = row["tier"]
        found.append(
            Member(
                endpoint,  # pyright: ignore[reportArgumentType]
                NOT_ASSESSED if endpoint is None else NONE,
                (None if tier == "?" else tier, row["age"]),  # pyright: ignore[reportArgumentType]
                (NOT_ASSESSED if tier == "?" else NONE, NONE),
            )
        )
    return found


def test_the_values_are_the_model_of_the_members_cells_found_from_the_rows(
    analyse: Analyse, store: Store, rows: Rows
) -> None:
    [analysed] = analyse(document(params={"covariates": [TIER, AGE]}), store(customers=60))
    given = rows_with_extras(rows, 60)
    positions = [
        members_from_rows(given, lambda age: age < 50),
        members_from_rows(given, lambda age: age >= 50),
    ]
    expected = cox.model(
        positions,
        [Covariate("category"), Covariate("number")],
        stratified=False,
        reference=0,
        overlap=False,
        level=0.95,
    )
    assert analysed.outcome.values == expected.values
    assert analysed.outcome.analysed == expected.analysed
    assert analysed.result.analysed == expected.analysed
    terms = expected.values.view.terms
    assert [(term.kind, term.position, term.covariate) for term in terms] == [
        ("cohort", 1, None),
        ("covariate", None, 0),
        ("covariate", None, 0),
        ("covariate", None, 1),
    ]
    assert any(term.estimate is not None for term in terms)


def test_cohorts_alone_give_survival_km_s_hazard_ratios_and_test(
    analyse: Analyse, store: Store
) -> None:
    written = document()
    written["views"].append({"analysis": "survival.km", "cohorts": ["young", "old"]})
    cox_view, km_view = analyse(written, store(customers=60))
    [term] = cox_view.outcome.values.view.terms
    [_, hazard] = km_view.outcome.values.view.effects
    assert (term.estimate, term.ci.low, term.ci.high) == (
        hazard.estimate,
        hazard.ci.low,
        hazard.ci.high,
    )
    km_test = km_view.outcome.values.view.proportional_hazards
    cox_test = cox_view.outcome.values.view.proportional_hazards
    assert (cox_test.statistic, cox_test.p) == (km_test.statistic, km_test.p)


def test_a_view_of_cohorts_alone_shares_survival_km_s_listing(check: Check, store: Store) -> None:
    written = document()
    written["views"].append({"analysis": "survival.km", "cohorts": ["young", "old"]})
    cox_view, km_view = check(written, store()).views
    assert [v.resolved for v in cox_view.variables] == []
    assert [e.form for e in cox_view.endpoints] == [e.form for e in km_view.endpoints]


def test_a_stratified_model_with_every_kind_of_covariate_runs(
    analyse: Analyse, store: Store
) -> None:
    covariates = [
        {"column": "customers.size"},
        {"column": "customers.vip"},
        {"column": "orders.channel", "aggregate": "some", "values": ["web"]},
        {"column": "orders.amount", "aggregate": "mean"},
        {"column": "orders.priority", "aggregate": "max"},
    ]
    params = {"covariates": covariates, "stratum": TIER}
    [analysed] = analyse(document(params=params), store(customers=90))
    view = analysed.outcome.values.view
    sizes = [term for term in view.terms if term.covariate == 0]
    assert [(t.level.data, t.baseline.data) for t in sizes if t.level and t.baseline] in (
        [(level, baseline) for level in SIZES if level != baseline] for baseline in SIZES
    )
    vip = [term for term in view.terms if term.covariate == 1]
    assert [(t.level.data, t.baseline.data) for t in vip if t.level and t.baseline] == [
        ("true", "false")
    ]
    priorities = [t.level.data for t in view.terms if t.covariate == 4 and t.level]
    assert priorities == [level for level in PRIORITIES if level in priorities]
    widths = Counter(term.covariate for term in view.terms if term.kind == "covariate")
    assert [test.covariate for test in view.covariate_tests] == [
        j for j in range(len(covariates)) if widths[j] >= 2
    ]
    assert widths[0] == 2


def test_the_values_do_not_depend_on_the_order_of_the_rows(analyse: Analyse, store: Store) -> None:
    written = document(params={"covariates": [TIER, AGE]})
    [first] = analyse(written, store(customers=60))
    shuffled = store(
        customers=60,
        edit=lambda given: random.Random(5).shuffle(given["customers"]),
    )
    [again] = analyse(written, shuffled)
    assert again.result.digest == first.result.digest


def test_the_same_view_gives_the_same_digest_every_time(analyse: Analyse, store: Store) -> None:
    written = document(params={"covariates": [TIER, AGE], "stratum": {"column": "customers.vip"}})
    digests = {analyse(written, store(customers=60))[0].result.digest for _ in range(2)}
    assert len(digests) == 1


# --- Run-time refusals (D368) --------------------------------------------------------------------


def edited(
    **columns: Callable[[int], object],
) -> Callable[[dict[str, list[dict[str, object]]]], None]:
    """An edit that sets each named customers column to its function of the customer's number."""

    def edit(given: dict[str, list[dict[str, object]]]) -> None:
        for n, customer in enumerate(given["customers"], 1):
            for name, value in columns.items():
                customer[name] = value(n)

    return edit


def test_covariates_that_code_to_more_than_eight_columns_are_refused(
    analyse: Analyse, store: Store
) -> None:
    notes = store(customers=60, edit=edited(note=lambda n: f"n{n % 10}"))
    with pytest.raises(cox.TooManyParameters) as raised:
        analyse(document(params={"covariates": [{"column": "customers.note"}]}), notes)
    assert raised.value.count == 9
    fewer = store(customers=60, edit=edited(note=lambda n: f"n{n % 9}"))
    analyse(document(params={"covariates": [{"column": "customers.note"}]}), fewer)


def test_a_stratum_of_more_than_a_hundred_levels_is_refused(analyse: Analyse, store: Store) -> None:
    many = store(customers=250, edit=edited(note=lambda n: f"n{n % 101}"))
    params = {"covariates": [AGE], "stratum": {"column": "customers.note"}}
    with pytest.raises(cox.TooManyStrata) as raised:
        analyse(document(params=params), many)
    assert raised.value.count == 101


def test_a_number_beyond_2_53_among_the_complete_cases_is_refused_and_elsewhere_is_not(
    analyse: Analyse, store: Store
) -> None:
    score = {"covariates": [{"column": "customers.score"}, TIER]}
    huge = store(customers=40, edit=edited(score=lambda n: 2.0**60 if n == 7 else float(n % 3)))
    with pytest.raises(cox.TooLarge) as raised:
        analyse(document(params=score), huge)
    assert raised.value.covariate == 0
    excluded = store(customers=40, edit=edited(score=lambda n: 2.0**60 if n == 5 else float(n % 3)))
    [analysed] = analyse(document(params=score), excluded)
    assert analysed.outcome.analysed[0].excluded_units + analysed.outcome.analysed[1].excluded_units


def test_a_number_the_fit_cannot_scale_is_refused_not_labelled(
    analyse: Analyse, store: Store
) -> None:
    tiny = store(customers=40, edit=edited(score=lambda n: 1e-200 * (n % 2)))
    with pytest.raises(cox.Unscalable) as raised:
        analyse(document(params={"covariates": [AGE, {"column": "customers.score"}]}), tiny)
    assert raised.value.covariate == 1


def test_a_view_whose_values_are_labelled_before_any_fit_never_scales_a_column(
    analyse: Analyse, store: Store
) -> None:
    tiny = store(customers=40, edit=edited(score=lambda n: 1e-200 * (n % 2), left=lambda _: "no"))
    [analysed] = analyse(document(params={"covariates": [{"column": "customers.score"}]}), tiny)
    assert {term.reasons()["/estimate"] for term in analysed.outcome.values.view.terms} == {
        "no_events"
    }


def test_a_level_longer_than_a_result_writes_is_refused(analyse: Analyse, store: Store) -> None:
    long = store(customers=40, edit=edited(note=lambda n: "x" * (MAX_TEXT + 1) if n % 2 else "y"))
    with pytest.raises(cox.LongLevel) as raised:
        analyse(document(params={"covariates": [AGE, {"column": "customers.note"}]}), long)
    assert raised.value.covariate == 1
    fits = store(customers=40, edit=edited(note=lambda n: "x" * MAX_TEXT if n % 2 else "y"))
    analyse(document(params={"covariates": [AGE, {"column": "customers.note"}]}), fits)


def test_a_time_no_output_holds_is_refused_as_survival_km_refuses_it(
    analyse: Analyse, store: Store
) -> None:
    far = store(customers=40, edit=edited(tenure=lambda n: 2.0**60 if n == 4 else float(n)))
    with pytest.raises(survival.TooLarge):
        analyse(document(params={"covariates": [AGE]}), far)


# --- Members' cells (D368) -----------------------------------------------------------------------


def test_members_cells_are_each_variable_s_value_or_reasons_then_the_endpoint_row(
    check: Check, store: Store
) -> None:
    [view] = check(document({"all": [EVERYONE]}, {"covariates": [TIER]}), store()).views
    [endpoint] = view.endpoints
    [event] = endpoint.event
    [censored] = endpoint.censored
    values: tuple[tuple[Any, ...], ...] = (
        ("gold", None, "silver"),
        (3.0, 4.0, 5.0),
        (event, censored, "?"),
        (0.0, 1.0, 0.0),
    )
    reasons = (
        (NONE, NOT_ASSESSED, NONE),
        (NONE, NONE, NONE),
        (NONE, NONE, NOT_ASSESSED),
        (NONE, NONE, NONE),
    )
    given = Listed([("a",), ("b",), ("c",)], [0, 1, 2], values, reasons, (frozenset(),) * 4)
    found = cox.members_of(endpoint, given, 1)
    assert found == [
        Member((0.0, 3.0, True), NONE, ("gold",), (NONE,)),
        Member((1.0, 4.0, False), NONE, (None,), (NOT_ASSESSED,)),
        Member(None, NOT_ASSESSED, ("silver",), (NONE,)),
    ]


def test_duplicate_covariates_and_a_covariate_equal_to_the_stratum_are_not_identified(
    analyse: Analyse, store: Store
) -> None:
    [twice] = analyse(document(params={"covariates": [AGE, AGE]}), store(customers=60))
    ages = [term for term in twice.outcome.values.view.terms if term.kind == "covariate"]
    assert {term.reasons()["/estimate"] for term in ages} == {"zero_variance"}
    params = {"covariates": [{"column": "customers.vip"}], "stratum": {"column": "customers.vip"}}
    [within] = analyse(document(params=params), store(customers=60))
    [vip] = [term for term in within.outcome.values.view.terms if term.kind == "covariate"]
    assert vip.reasons()["/estimate"] == "zero_variance"


# --- Caveats (D369) ----------------------------------------------------------------------------


def test_a_tier_not_assessed_and_a_status_not_assessed_carry_unknown_excluded(
    analyse: Analyse, store: Store
) -> None:
    [analysed] = analyse(document(params={"covariates": [TIER]}), store(customers=60))
    [unknown] = [c for c in analysed.result.caveats if c.code == "UNKNOWN_EXCLUDED"]
    assert unknown.affects == ["/analysed", "/population"]
    excluded = analysed.outcome.analysed[0].excluded
    assert excluded[ExclusionReason.NOT_ASSESSED] > 0
    assert "INVALID_EXCLUDED" not in codes(analysed.result)


def test_an_invalid_endpoint_row_carries_invalid_excluded(analyse: Analyse, store: Store) -> None:
    invalid = store(customers=40, edit=edited(joined=lambda n: 99.0 if n == 4 else 0.0))
    [analysed] = analyse(document(params={"covariates": [AGE]}), invalid)
    assert "INVALID_EXCLUDED" in codes(analysed.result)
    assert "UNKNOWN_EXCLUDED" in codes(analysed.result)


def test_cohorts_that_share_units_under_overlap_allow_leave_every_value_overlapping(
    analyse: Analyse, store: Store
) -> None:
    written = document({"all": [EVERYONE], "old": [OLD]}, {"covariates": [AGE]}, overlap="allow")
    [analysed] = analyse(written, store(customers=40))
    view = analysed.outcome.values.view
    assert {term.reasons()["/estimate"] for term in view.terms} == {"overlapping_cohorts"}
    assert "COHORTS_OVERLAP" in codes(analysed.result)


@pytest.mark.parametrize(
    ("group", "events", "small"), [(10, 5, False), (11, 5, True), (10, 6, True)]
)
def test_a_cohort_with_few_complete_cases_or_events_carries_small_n(
    analyse: Analyse,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
    group: int,
    events: int,
    small: bool,
) -> None:
    [first] = analyse(document(params={"covariates": [AGE]}), store(customers=24))
    n = [one.n for one in first.outcome.analysed]
    held = [at.events for at in first.outcome.values.positions]
    monkeypatch.setattr(cox, "MIN_GROUP_N", min(n) + group - 10)
    monkeypatch.setattr(cox, "MIN_EVENTS", min(held) + events - 5)
    monkeypatch.setattr(cox, "EVENTS_PER_TERM", 0)
    [analysed] = analyse(document(params={"covariates": [AGE]}), store(customers=24))
    affected = [
        affected
        for caveat in analysed.result.caveats
        if caveat.code == "SMALL_N"
        for affected in caveat.affects
    ]
    assert bool(affected) is small
    assert "/values/view/terms" not in affected


def test_fewer_than_ten_events_per_estimated_term_carry_small_n_on_the_terms(
    analyse: Analyse, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    written = document(params={"covariates": [AGE, TIER]})
    [first] = analyse(written, store(customers=60))
    events = sum(at.events for at in first.outcome.values.positions)
    estimated = sum(term.estimate is not None for term in first.outcome.values.view.terms)
    assert estimated >= 2
    monkeypatch.setattr(cox, "MIN_GROUP_N", 0)
    monkeypatch.setattr(cox, "MIN_EVENTS", 0)
    for per_term, small in ((events / estimated, False), (events / estimated + 0.5, True)):
        monkeypatch.setattr(cox, "EVENTS_PER_TERM", per_term)
        [analysed] = analyse(written, store(customers=60))
        affected = [c.affects for c in analysed.result.caveats if c.code == "SMALL_N"]
        assert affected == ([["/values/view/terms"]] if small else [])


def test_a_test_of_proportional_hazards_below_the_level_carries_ph_violated(
    analyse: Analyse, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    written = document(params={"covariates": [AGE]})
    [first] = analyse(written, store(customers=60))
    p = first.outcome.values.view.proportional_hazards.p
    assert p is not None
    for level, violated in ((p, False), (math.nextafter(p, 1), True)):
        monkeypatch.setattr(cox, "PH_LEVEL", level)
        [analysed] = analyse(written, store(customers=60))
        found = [c for c in analysed.result.caveats if c.code == "PH_VIOLATED"]
        assert bool(found) is violated
        if found:
            assert sorted(found[0].affects) == [
                "/values/view/proportional_hazards",
                "/values/view/terms",
            ]


def test_every_caveat_the_view_can_raise_is_one_its_entry_lists() -> None:
    assert {code.value for code in cox.CAVEATS} >= {
        "UNKNOWN_EXCLUDED",
        "INVALID_EXCLUDED",
        "COHORTS_OVERLAP",
        "SMALL_N",
        "PH_VIOLATED",
        "NOT_ESTIMABLE",
        "UNCONFIRMED_SEMANTICS",
        "DRAFT_RELEASE",
        "SCOPE_PARTIAL",
        "COVERAGE_PROPOSED",
        "LIFT_DIFFERS",
    }
    assert cox.ENTRY.fields.caveats == [code.value for code in cox.CAVEATS]
    assert set(cox.ENTRY.fields.methods) == {cox.WALD, cox.CONE, cox.TEST}


# --- Readback and chart (D369) -------------------------------------------------------------------


def test_the_readback_names_the_endpoint_the_terms_and_how_each_covariate_enters(
    check: Check, store: Store
) -> None:
    covariates = [
        {"column": "customers.size"},
        {"column": "customers.vip"},
        AGE,
        {"column": "orders.order_id", "aggregate": "count"},
        {"column": "customers.score"},
    ]
    params = {"covariates": covariates, "stratum": TIER, "level": 0.9}
    [view] = check(document(params=params), store()).views
    text = said(view.readback())
    assert text.startswith(
        "A Cox proportional hazards model, Efron ties, of the endpoint ep:retention: the time "
        "customers.tenure in mo, the status customers.left"
    )
    assert "each unit entering at customers.joined" in text
    assert (
        "; its terms: each other cohort's membership, of the 2 in view order, versus the "
        "reference, the cohort at position 0" in text
    )
    assert "; covariate 0 (one term per level against" in text
    assert "; covariate 2 (a number, its hazard ratio per 1 more, in a): " in text
    assert "its levels in their declared order" in text
    assert "1 for true against 0 for false" in text
    assert "its hazard ratio per row more" in text
    assert text.count("its hazard ratio per 1 more") == 2
    assert "; stratified by the levels of " in text
    assert "Wald intervals at 0.9" in text
    assert "young" not in text
    assert "old" not in text
    [one] = check(document({"all": [EVERYONE]}, {"covariates": [AGE]}), store()).views
    assert "membership" not in said(one.readback())


def test_survival_km_s_readback_keeps_its_words(check: Check, store: Store) -> None:
    written = document()
    written["views"] = [{"analysis": "survival.km", "cohorts": ["young", "old"]}]
    [view] = check(written, store()).views
    assert said(view.readback()).startswith(
        "Kaplan–Meier survival of the endpoint ep:retention: the time customers.tenure in mo, "
        'the status customers.left ("yes" an event, "no" censored), each unit entering at '
        "customers.joined. For each cohort, the curve"
    )


def test_the_chart_draws_what_the_terms_show_labelled_so_that_no_label_is_another_s(
    analyse: Analyse, store: Store
) -> None:
    [analysed] = analyse(document(params={"covariates": [TIER, AGE]}), store(customers=60))
    [chart] = analysed.result.charts
    rules, points = (layer["data"]["values"] for layer in chart["layer"])
    terms = analysed.outcome.values.view.terms

    def label(term: Any) -> str:
        if term.kind == "cohort":
            return "cohort old"
        if term.level is None:
            return f"covariate {term.covariate}"
        return f'covariate {term.covariate}: "{term.level.data}" vs "{term.baseline.data}"'

    shown = [term for term in terms if term.estimate is not None]
    bounded = [term for term in terms if term.ci.low is not None and term.ci.high is not None]
    assert [row["term"] for row in points] == [label(term) for term in shown]
    assert [row["estimate"] for row in points] == [term.estimate for term in shown]
    assert [(row["low"], row["high"]) for row in rules] == [(t.ci.low, t.ci.high) for t in bounded]
    assert len({label(term) for term in terms}) == len(terms)
    assert {"covariate 1", "cohort old"} <= {row["term"] for row in points}
    assert "$schema" not in json.dumps(chart)


def test_a_term_with_an_estimate_but_a_bound_beyond_range_keeps_its_point() -> None:
    values = CoxValues.model_validate(
        {
            "positions": [
                {
                    "events": 3,
                    "variables": [
                        {
                            "n": 5,
                            "excluded": dict.fromkeys(ExclusionReason, 0),
                            "excluded_units": 0,
                        }
                    ],
                }
            ],
            "view": {
                "terms": [
                    {
                        "kind": "covariate",
                        "covariate": 0,
                        "estimate": 3.0,
                        "ci": {"method": "wald", "level": 0.95, "low": 1.0, "high": None},
                        "p": 0.2,
                        "not_estimable": {"/ci/high": "separation"},
                    },
                    {
                        "kind": "covariate",
                        "covariate": 1,
                        "estimate": None,
                        "ci": {"method": "wald", "level": 0.95, "low": None, "high": None},
                        "p": None,
                        "not_estimable": {
                            "/estimate": "zero_variance",
                            "/ci/low": "zero_variance",
                            "/ci/high": "zero_variance",
                            "/p": "zero_variance",
                        },
                    },
                ],
                "covariate_tests": [],
                "proportional_hazards": {
                    "method": "grambsch_therneau",
                    "terms": [0],
                    "statistic": 0.5,
                    "df": 1,
                    "p": 0.48,
                },
            },
        }
    )
    chart: Any = cox_chart(values, ["a"])
    rules, points = (layer["data"]["values"] for layer in chart["layer"])
    assert points == [{"index": 0, "term": "covariate 0", "estimate": 3.0}]
    assert rules == []


# --- The deadline (D350) ----------------------------------------------------------------------


def _looks(
    check: Check, store: Store, monkeypatch: pytest.MonkeyPatch, passing: int | None
) -> tuple[Counter[str], int]:
    """Each function that looks at the deadline in a stratified view of a category and a number,
    with delayed entry, each loop looking every time, and how many looks it made; with
    ``passing``, the deadline passes at that look."""
    written = document(params={"covariates": [TIER, AGE], "stratum": {"column": "customers.vip"}})
    [view] = check(written, store(customers=40)).views
    [endpoint] = view.endpoints
    read = [*(v.resolved for v in view.variables), *endpoint.variables]
    listings = [ordered(listed(cohort.resolved, read)) for cohort in view.cohorts]
    positions = [CohortAt(cohort, evaluate(cohort.resolved)) for cohort in view.cohorts]
    sites: set[str] = set()
    looks: list[int] = []

    def clock() -> float:
        frame = sys._getframe(1)  # pyright: ignore[reportPrivateUsage]
        while frame.f_code.co_qualname in ("Watch._read", "Watch.look", "Watch.spend"):
            assert frame.f_back is not None
            frame = frame.f_back
        sites.add(frame.f_code.co_qualname)
        looks.append(1)
        return 2.0 if passing is not None and len(looks) > passing else 0.0

    monkeypatch.setattr(timetoevent, "time", type("Clock", (), {"monotonic": staticmethod(clock)}))
    monkeypatch.setattr(timetoevent, "LOOK_EVERY", 1)
    covariates = [Covariate("category"), Covariate("number")]
    params = view.params
    assert isinstance(params, CoxParams)
    try:
        cox.analyse(
            positions, listings, endpoint, covariates, params, reference=0, overlap=False, ends=1.0
        )
    except CallerDeadline:
        assert passing is not None
        return Counter(sites), len(looks)
    assert passing is None
    return Counter(sites), len(looks)


def test_every_pass_of_the_analysis_looks_at_the_deadline(
    check: Check, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    found, _ = _looks(check, store, monkeypatch, None)
    assert {"endpoint_cells", "members_of", "_within", "analyse", "model", "_analysed"} <= set(
        found
    )


def test_the_deadline_passed_at_any_look_stops_the_analysis_there(
    check: Check, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, total = _looks(check, store, monkeypatch, None)
    for passing in sorted({0, 1, 2, total // 3, total // 2, total - 1}):
        _looks(check, store, monkeypatch, passing)


def test_levels_are_ordered_by_canonical_text_as_utf_16_code_units() -> None:
    assert sorted(["b", "a", "é", "Z"], key=order_key) == ["Z", "a", "b", "é"]


# --- Refusals as written, exact limits and the cells' reasons (D366–D369) -------------------------


@pytest.mark.parametrize(
    ("covariates", "given", "expected"),
    [
        ("$p", [{"column": "customers.since"}], "/views/0/params/covariates"),
        (["$p"], {"column": "customers.since"}, "/views/0/params/covariates/0"),
        (["$p"], {"column": "customers.age", "bins": [0, 50]}, "/views/0/params/covariates/0"),
        (["$p"], {"column": "customers.customer_id"}, "/views/0/params/covariates/0"),
    ],
    ids=["the list", "a date", "bins", "an identifier"],
)
def test_a_refusal_in_a_parameter_s_value_is_pointed_at_its_reference(
    check: Check, store: Store, covariates: Any, given: Any, expected: str
) -> None:
    written = document(params={"covariates": covariates})
    written["params"] = {"p": given}
    [refusal] = check(written, store()).refusals
    assert refusal.path == expected
    assert {"data": "p"} in [segment.model_dump() for segment in refusal.message]


def test_a_refusal_of_another_analysis_s_variable_in_a_parameter_is_pointed_at_it_too(
    check: Check, store: Store
) -> None:
    written = document(params={})
    written["views"] = [
        {"analysis": "compare.columns", "cohorts": ["young", "old"], "params": {"columns": ["$c"]}}
    ]
    written["params"] = {"c": {"column": "customers.tier", "bins": [0, 1]}}
    [refusal] = check(written, store()).refusals
    assert (refusal.code, refusal.path) == (RefusalCode.INVALID_VALUE, "/views/0/params/columns/0")


def members(values: Sequence[Any], events: Sequence[bool]) -> list[Member]:
    return [
        Member((ORIGIN, float(i + 1), event), NONE, (value,), (NONE,))
        for i, (value, event) in enumerate(zip(values, events, strict=True))
    ]


@pytest.mark.parametrize(
    ("value", "refused"),
    [(float(MAX_SAFE_INTEGER), False), (-float(MAX_SAFE_INTEGER), False), (2.0**53, True)],
)
def test_a_number_is_refused_exactly_beyond_2_53_less_1(value: float, refused: bool) -> None:
    given = [value, 1.0, 2.0, 3.0, 4.0, 5.0]
    events = [True, False, True, True, False, True]
    positions = [members(given, events), members(given[::-1], events)]

    def run() -> Any:
        return cox.model(
            positions,
            [Covariate("number")],
            stratified=False,
            reference=0,
            overlap=False,
            level=0.95,
        )

    if refused:
        with pytest.raises(cox.TooLarge):
            run()
    else:
        run()


def test_a_long_baseline_is_refused_and_a_constant_long_level_is_not() -> None:
    long = "x" * (MAX_TEXT + 1)
    values = [long, long, long, "y", "y", long]
    events = [True, True, True, True, False, True]
    positions = [members(values, events), members(values, events)]
    with pytest.raises(cox.LongLevel):
        cox.model(
            positions,
            [Covariate("category")],
            stratified=False,
            reference=0,
            overlap=False,
            level=0.95,
        )
    constant = [members([long] * 6, events), members([long] * 6, events)]
    found = cox.model(
        constant, [Covariate("category")], stratified=False, reference=0, overlap=False, level=0.95
    )
    [_, term] = found.values.view.terms
    assert (term.level, term.baseline, term.reasons()["/estimate"]) == (None, None, "zero_variance")


def test_an_entry_no_output_holds_is_refused_as_survival_km_refuses_it(
    analyse: Analyse, store: Store
) -> None:
    far = store(customers=40, edit=edited(joined=lambda n: -(2.0**60) if n == 4 else 0.0))
    with pytest.raises(survival.TooLarge):
        analyse(document(params={"covariates": [AGE]}), far)


def test_a_cohort_without_complete_cases_carries_no_small_n(
    analyse: Analyse, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    def untiered(given: dict[str, list[dict[str, object]]]) -> None:
        for customer in given["customers"]:
            if int(str(customer["age"])) < 50:
                customer["tier"] = "?"

    monkeypatch.setattr(cox, "MIN_GROUP_N", 1000)
    [analysed] = analyse(document(params={"covariates": [TIER]}), store(edit=untiered))
    assert analysed.outcome.analysed[0].n == 0
    [small] = [c for c in analysed.result.caveats if c.code == "SMALL_N"]
    assert small.affects == ["/values/positions/1"]


def test_a_term_the_fit_estimated_is_one_with_a_ratio_or_one_beyond_range() -> None:
    def term(reason: str | None, estimate: float | None, direction: str | None) -> Any:
        marks = None if reason is None else dict.fromkeys(("/estimate",), reason)
        return CoxTerm.model_validate(
            {
                "kind": "covariate",
                "covariate": 0,
                "estimate": estimate,
                "ci": {"method": "wald", "level": 0.95, "low": None, "high": None},
                "p": None,
                "direction": direction,
                "not_estimable": {
                    **(marks or {}),
                    "/ci/low": "separation",
                    "/ci/high": "separation",
                    "/p": "zero_variance",
                },
            }
        )

    estimated = cox._estimated  # pyright: ignore[reportPrivateUsage]
    assert estimated(term(None, 2.0, None))
    assert estimated(term("separation", None, None))
    assert not estimated(term("separation", None, "infinity"))
    assert not estimated(term("zero_variance", None, None))


def test_the_flags_of_the_cells_listed_are_the_values_caveats(check: Check, store: Store) -> None:
    [view] = check(document({"all": [EVERYONE]}, {"covariates": [AGE]}), store()).views
    [endpoint] = view.endpoints
    [event] = endpoint.event
    [censored] = endpoint.censored
    count = 12
    mark = Mark(Flag.SCOPE_PARTIAL, "rel:orders.customer")
    none = tuple(NONE for _ in range(count))
    values: tuple[tuple[Any, ...], ...] = (
        tuple(float(n % 5) for n in range(count)),
        tuple(float(n + 1) for n in range(count)),
        tuple(event if n % 3 else censored for n in range(count)),
        tuple(0.0 for _ in range(count)),
    )
    given = Listed(
        [(str(n),) for n in range(count)],
        list(range(count)),
        values,
        (none,) * 4,
        (frozenset({mark}), frozenset(), frozenset(), frozenset()),
    )
    params = view.params
    assert isinstance(params, CoxParams)
    positions = [CohortAt(cohort, evaluate(cohort.resolved)) for cohort in view.cohorts]
    found = cox.analyse(
        positions, [given], endpoint, [Covariate("number")], params, reference=0, overlap=False
    )
    flagged = [c for c in found.caveats if c.code == "SCOPE_PARTIAL"]
    assert [c.affects for c in flagged] == [["/values"]]


def test_the_chart_is_on_a_log_scale_its_rows_in_the_terms_order() -> None:
    def term(covariate: int, estimate: float | None, low: float | None, high: float | None) -> Any:
        marks = {
            member: "separation"
            for member, value in (("/estimate", estimate), ("/ci/low", low), ("/ci/high", high))
            if value is None
        }
        return {
            "kind": "covariate",
            "covariate": covariate,
            "estimate": estimate,
            "ci": {"method": "wald", "level": 0.95, "low": low, "high": high},
            "p": 0.5,
            **({"not_estimable": marks} if marks else {}),
        }

    values = CoxValues.model_validate(
        {
            "positions": [
                {
                    "events": 3,
                    "variables": [
                        {"n": 5, "excluded": dict.fromkeys(ExclusionReason, 0), "excluded_units": 0}
                    ],
                }
            ],
            "view": {
                "terms": [
                    term(0, 2.0, None, 5.0),
                    term(1, 1.5, 1.0, 2.0),
                    term(2, None, None, None),
                ],
                "covariate_tests": [],
                "proportional_hazards": {
                    "method": "grambsch_therneau",
                    "terms": [0, 1],
                    "statistic": 0.5,
                    "df": 2,
                    "p": 0.7,
                },
            },
        }
    )
    chart: Any = cox_chart(values, ["a"])
    rules, points = (layer["data"]["values"] for layer in chart["layer"])
    assert [row["term"] for row in points] == ["covariate 0", "covariate 1"]
    assert [row["term"] for row in rules] == ["covariate 1"]
    for layer in chart["layer"]:
        encoding = layer["encoding"]
        assert encoding["x"]["scale"] == {"type": "log"}
        assert encoding["y"]["scale"] == {"domain": ["covariate 0", "covariate 1"]}


def test_the_entry_requires_an_endpoint_on_the_unit_and_one_to_six_cohorts() -> None:
    requires = [r.model_dump(exclude_none=True) for r in cox.ENTRY.fields.requires]
    assert requires == [
        {"role": "endpoint", "kind": "endpoint", "on": "unit"},
        {"role": "cohorts", "min": 1, "max": 6},
    ]
    assert cox.ENTRY.fields.uses_reference
    assert cox.ENTRY.fields.assumes_independent_groups


@pytest.mark.parametrize(
    ("time_at", "refused"), [(float(MAX_SAFE_INTEGER), False), (2.0**53, True)]
)
def test_a_time_is_refused_exactly_beyond_2_53_less_1(
    analyse: Analyse, store: Store, time_at: float, refused: bool
) -> None:
    given = store(customers=40, edit=edited(tenure=lambda n: time_at if n == 4 else float(n)))
    if refused:
        with pytest.raises(survival.TooLarge):
            analyse(document(params={"covariates": [AGE]}), given)
    else:
        analyse(document(params={"covariates": [AGE]}), given)


@pytest.mark.parametrize("reason", [ExclusionReason.NOT_APPLICABLE, ExclusionReason.INVALID_VALUE])
def test_units_left_out_for_a_reason_that_is_not_unknown_carry_no_unknown_excluded(
    check: Check, store: Store, reason: ExclusionReason
) -> None:
    [view] = check(document({"all": [EVERYONE]}, {"covariates": [AGE]}), store()).views
    [endpoint] = view.endpoints
    [event] = endpoint.event
    [censored] = endpoint.censored
    count = 12
    left = frozenset({reason})
    values: tuple[tuple[Any, ...], ...] = (
        tuple(None if n == 0 else float(n % 5) for n in range(count)),
        tuple(float(n + 1) for n in range(count)),
        tuple(event if n % 3 else censored for n in range(count)),
        tuple(0.0 for _ in range(count)),
    )
    reasons = (
        tuple(left if n == 0 else NONE for n in range(count)),
        (NONE,) * count,
        (NONE,) * count,
        (NONE,) * count,
    )
    given = Listed(
        [(str(n),) for n in range(count)], list(range(count)), values, reasons, (frozenset(),) * 4
    )
    params = view.params
    assert isinstance(params, CoxParams)
    positions = [CohortAt(cohort, evaluate(cohort.resolved)) for cohort in view.cohorts]
    found = cox.analyse(
        positions, [given], endpoint, [Covariate("number")], params, reference=0, overlap=False
    )
    assert found.analysed[0].excluded_units == 1
    assert "UNKNOWN_EXCLUDED" not in {caveat.code for caveat in found.caveats}


def test_the_chart_shows_levels_as_they_are_written(analyse: Analyse, store: Store) -> None:
    accented = store(customers=60, edit=edited(note=lambda n: ("café", "東京")[n % 2]))
    params = {"covariates": [{"column": "customers.note"}]}
    [analysed] = analyse(document(params=params), accented)
    [chart] = analysed.result.charts
    labels = [row["term"] for layer in chart["layer"] for row in layer["data"]["values"]]
    assert any('"café"' in label and '"東京"' in label for label in labels)


def test_a_refusal_under_a_key_that_holds_a_slash_or_a_tilde_keeps_its_escapes() -> None:
    refusal = Refusal(
        code=RefusalCode.INVALID_VALUE,
        path="/views/0/params/options/a~1b~0c/x",
        message=[],
    )
    found = refusal_as_written(refusal, {("views", 0, "params", "options", "a/b~c"): "p"})
    assert found.path == "/views/0/params/options/a~1b~0c"
    assert {"data": "p"} in [segment.model_dump() for segment in found.message]
    assert refusal_as_written(refusal, {}) == refusal
    assert refusal_as_written(refusal, {("views", 1): "q"}) == refusal


def test_the_range_of_the_numbers_is_a_pass_that_looks_at_the_deadline(
    check: Check, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    found, _ = _looks(check, store, monkeypatch, None)
    assert "_in_range" in found

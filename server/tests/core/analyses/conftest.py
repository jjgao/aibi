"""The analysis tests' world: a shop, built in memory (SPEC P8: no domain), and helpers that run a
document's views by the reference evaluator.

Customers have a tier and an age. They place orders, all recorded, through a channel; an order's
returns are recorded only for the orders a check lists (direct coverage), and never for phone
orders (a parent scope), so that a question about returns through orders is one whose two lift
rules differ (§6.5).

- ``shop`` builds the release from rows (``rows`` the default rows, ``customers`` of them).
- ``analyse`` loads a document, checks its views, canonicalises its cohorts, predicates,
  variables and endpoints, runs each by the reference evaluator (``evaluate``,
  ``evaluate_variable``, ``members.keys``, ``inputs.listed`` for a pack's or a survival analysis,
  a pack's registry given) and makes its result envelope as ``run_analysis`` does, returning each
  view's ``Analysed``. ``shop(extended=True)`` adds an order's amount, a number with a declared
  range, and declares the customers' ages' range, for ``summary.distribution``;
  ``shop(survived=...)`` adds each customer's months until they left (``tenure``, ``left``,
  ``joined``) and the endpoint ``ep:retention`` over them, its customers entering at ``joined``
  (``"delayed"``), at the origin (``"origin"``) or as it leaves undeclared (``"undeclared"``).
- ``distributed`` checks a ``summary.distribution`` view of the extended shop over cohorts of the
  pattern document, and ``summarised`` runs it over variables materialised as given, under a
  *k*, each cohort's size given, for checks that run one view many times; ``columned`` and
  ``contrasted`` do the same for ``compare.columns``.
- ``patterned`` makes cohorts and predicates whose units' truth values are given as strings, one
  character per unit (``T``, ``F``, or a reason's letter for UNKNOWN: ``I`` NO_INFORMATION, ``A``
  NOT_ASSESSED, ``C`` NOT_COVERED), each over a canonical cohort of the shop, and runs
  ``compare.existence`` over them under a *k*, returning the outcome and its envelope;
  ``viewed`` checks such a view once and ``compared`` runs it over given patterns, for checks
  that run one view many times.

Test modules can't import one another (``--import-mode=importlib``), so the helpers are fixtures.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import pytest

from aibi.core.analyses import columns, cox, distribution, members, packs, survival, views
from aibi.core.analyses.existence import CohortAt, Outcome, compare
from aibi.core.analyses.registry import Analyses
from aibi.core.analyses.results import Outcome as AnyOutcome
from aibi.core.analyses.results import envelope
from aibi.core.analyses.views import CheckedView
from aibi.core.engine import build
from aibi.core.engine.canonical import Canonicalisation, canonicalise
from aibi.core.engine.data import Release
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.inputs import listed, shared
from aibi.core.engine.inputs import ordered as ordered_inputs
from aibi.core.engine.members import keys, ordered
from aibi.core.engine.resolve import Label, ResolvedCohort
from aibi.core.engine.resolved import flipped
from aibi.core.engine.sql import Accounting, Crossing, TruthValues, cross
from aibi.core.engine.truth import Truth, TruthValue
from aibi.core.engine.variables import Joint, Materialised, evaluate_variable, joint, materialise
from aibi.core.schema.analyses import (
    ColumnsParams,
    CoxParams,
    DistributionParams,
    ExistenceParams,
    MembersParams,
    PackParams,
    SurvivalParams,
)
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.loading import load_document
from aibi.core.schema.refusals import Refusal
from aibi.core.schema.results import ResultEnvelope
from aibi.core.schema.semantics import Reason

ORDERS = "rel:orders.customer"
RETURNS = "rel:returns.order"
ENGINE = "aibi test"


ENTRIES: Mapping[str, Any] = {
    "delayed": {"column": "joined"},
    "origin": "at_origin",
    "undeclared": None,
}
"""How the shop's retention endpoint says its customers enter: at their ``joined`` month, at the
origin, or undeclared."""


def retention(entry: str = "delayed", **fields: Any) -> Descriptor:
    """The shop's endpoint: months until a customer leaves (``customers.tenure``, its status
    ``customers.left``), entering as ``entry`` says; ``fields`` replace its fields."""
    given: dict[str, Any] = {
        "table": "customers",
        "time_column": "tenure",
        "status_column": "left",
        "event_coding": {"event": ["yes"], "censored": ["no"]},
    }
    if ENTRIES[entry] is not None:
        given["entry"] = ENTRIES[entry]
    given.update(fields)
    return build.descriptor(
        "endpoint", "ep:retention", {k: v for k, v in given.items() if v is not None}
    )


def shop_descriptors(
    *,
    disclosure: Mapping[str, Any] | None = None,
    extended: bool = False,
    extras: Sequence[Descriptor] = (),
    survived: str | None = None,
) -> list[Descriptor]:
    column, table, relationship = build.column, build.table, build.relationship
    ages: dict[str, Any] = {"range": {"min": 18, "max": 98}} if extended else {}
    amounts = [column("orders.amount", "number", range={"min": 0, "max": 200})] if extended else []
    if survived is not None:
        amounts += [
            column("customers.tenure", "time_offset", units="mo"),
            column(
                "customers.left",
                "category",
                permissible_values={"values": [{"value": v} for v in ("yes", "no")]},
                missing_codes={"?": "NOT_ASSESSED"},
            ),
            column("customers.joined", "time_offset", units="mo"),
            retention(survived),
        ]
    amounts += extras
    return [
        build.dataset(**({} if disclosure is None else {"disclosure": dict(disclosure)})),
        table("customers", ["customer_id"]),
        column("customers.customer_id", "string", identifier=True),
        column(
            "customers.tier",
            "category",
            permissible_values={"values": [{"value": v} for v in ("gold", "silver", "bronze")]},
            missing_codes={"?": "NOT_ASSESSED"},
        ),
        column("customers.age", "integer", units="a", **ages),
        table("orders", ["order_id"], role="event"),
        column("orders.order_id", "string"),
        column("orders.customer_id", "string"),
        column(
            "orders.channel",
            "category",
            permissible_values={"values": [{"value": v} for v in ("shop", "web", "phone")]},
        ),
        *amounts,
        relationship("orders", ["customer_id"], "customers", role="customer"),
        build.coverage(ORDERS, "all"),
        table("returns", ["return_id"], role="event"),
        column("returns.return_id", "string"),
        column("returns.order_id", "string"),
        column(
            "returns.reason",
            "category",
            permissible_values={"values": [{"value": v} for v in ("size", "late", "broken")]},
        ),
        relationship("returns", ["order_id"], "orders", role="order"),
        build.coverage(
            RETURNS,
            {"table": "checked_orders", "parent_columns": {"order_id": "order_id"}},
            parent_scope={"kind": "value", "column": "orders.channel", "values": ["shop", "web"]},
        ),
        table("checked_orders", ["order_id"], role="coverage"),
        column("checked_orders.order_id", "string"),
    ]


def shop_rows(
    customers: int = 24, *, extended: bool = False, survived: bool = False
) -> dict[str, list[dict[str, object]]]:
    """Customers of three tiers, every fifth tier not assessed, of ages 20 to 69; each places
    an order or two, one in four through the phone; every other order is checked for returns,
    and one in three of the checked ones has one. ``extended``: every order has an amount but
    every seventh, whose amount is empty. ``survived``: each customer has a tenure of 1 to 37
    months and joined in month 0 to 4 of it (0 for every third), and two in three left (every
    eleventh's leaving not assessed)."""
    rows: dict[str, list[dict[str, object]]] = {
        "customers": [],
        "orders": [],
        "returns": [],
        "checked_orders": [],
    }
    tiers = ("gold", "silver", "bronze")
    order = 0
    for n in range(1, customers + 1):
        tier = "?" if n % 5 == 0 else tiers[n % 3]
        customer: dict[str, object] = {
            "customer_id": f"c{n}",
            "tier": tier,
            "age": 20 + (n * 7) % 50,
        }
        if survived:
            tenure = float((n * 11) % 37 + 1)
            customer["tenure"] = tenure
            customer["left"] = "?" if n % 11 == 0 else "yes" if n % 3 else "no"
            customer["joined"] = 0.0 if n % 3 == 0 else min(float(n % 5), tenure - 0.5)
        rows["customers"].append(customer)
        for _ in range(1 + n % 2):
            order += 1
            channel = "phone" if order % 4 == 0 else ("shop", "web")[order % 2]
            placed: dict[str, object] = {
                "order_id": f"o{order}",
                "customer_id": f"c{n}",
                "channel": channel,
            }
            if extended:
                placed["amount"] = None if order % 7 == 0 else (order * 37) % 150 + 0.5
            rows["orders"].append(placed)
            if order % 2:
                rows["checked_orders"].append({"order_id": f"o{order}"})
                if order % 3 == 0:
                    rows["returns"].append(
                        {"return_id": f"r{order}", "order_id": f"o{order}", "reason": "size"}
                    )
    return rows


def shop_release(
    rows: Mapping[str, Sequence[Mapping[str, object]]] | None = None, **options: Any
) -> Release:
    extended = bool(options.get("extended"))
    survived = options.get("survived") is not None
    given = rows if rows is not None else shop_rows(extended=extended, survived=survived)
    return build.release(shop_descriptors(**options), given)


@dataclass(frozen=True)
class Analysed:
    """A view run by the reference evaluator, and its result envelope."""

    view: CheckedView
    outcome: AnyOutcome
    result: ResultEnvelope


@dataclass(frozen=True)
class Checked:
    canonical: Canonicalisation
    views: list[CheckedView]
    refusals: list[Refusal]


def check_document(
    written: Mapping[str, Any],
    release: Release,
    *,
    floor: int | None = None,
    published: int | None = None,
    analyses: Analyses | None = None,
    label: Label = 1,
) -> Checked:
    """A document's cohorts and views canonicalised, as the query tools do them; ``published``
    is the release's floor of its own, as a draft's latest published release sets it."""
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None, loaded.refusals
    registry = analyses or Analyses()
    parsed, refused = views.parse(loaded.document, loaded.positions, registry)
    canonical = canonicalise(
        loaded.document,
        {written["dataset"]: release},
        labels={release.manifest: label},
        registry=registry.packs,
        floor=floor,
        floors={release.manifest: published},
        positions=loaded.positions,
        predicates=[p for view in parsed for p in view.predicates],
        variables=[v for view in parsed for v in view.variables],
        endpoints=[e for view in parsed for e in view.endpoints],
    )
    checked, mixed = views.checked(loaded.document, parsed, canonical, registry, loaded.positions)
    deferred = views.deferred(loaded.document, loaded.positions)
    return Checked(canonical, checked, [*refused, *deferred, *canonical.refusals, *mixed])


def _lifted(cohort: ResolvedCohort) -> TruthValues | None:
    other = tuple(flipped(clause) for clause in cohort.clauses)
    if other == cohort.clauses:
        return None
    return TruthValues.of(evaluate(replace(cohort, clauses=other)).values)


def analyse_document(
    written: Mapping[str, Any],
    release: Release,
    *,
    floor: int | None = None,
    analyses: Analyses | None = None,
) -> list[Analysed]:
    """Each view of a document run by the reference evaluator, as ``run_analysis`` runs it by
    SQL; the document must check. ``analyses`` holds the packs whose analyses a view names."""
    checked = check_document(written, release, floor=floor, analyses=analyses)
    assert checked.refusals == [], checked.refusals
    found: list[Analysed] = []
    for view in checked.views:
        positions = [CohortAt(cohort, evaluate(cohort.resolved)) for cohort in view.cohorts]
        if isinstance(view.params, PackParams):
            assert analyses is not None, "a pack's analysis is of an installed pack"
            found.append(_result(view, _packed_by_evaluator(view, positions, analyses), written))
            continue
        if isinstance(view.params, SurvivalParams):
            found.append(_result(view, _survived_by_evaluator(view, positions), written))
            continue
        if isinstance(view.params, CoxParams):
            found.append(_result(view, _coxed_by_evaluator(view, positions), written))
            continue
        if isinstance(view.params, DistributionParams):
            found.append(_result(view, _summarised_by_evaluator(view, positions), written))
            continue
        if isinstance(view.params, MembersParams):
            [position] = positions
            listed = ordered(keys(position.cohort.resolved))
            outcome = members.list_members(position, listed, view.params, k=view.disclosure)
            found.append(_result(view, outcome, written))
            continue
        if isinstance(view.params, ColumnsParams):
            found.append(_result(view, _compared_by_evaluator(view, positions), written))
            continue
        crossing = cross(
            [TruthValues.of(evaluate(cohort.resolved).values) for cohort in view.cohorts],
            [TruthValues.of(evaluate(p.resolved).values) for p in view.predicates],
            [_lifted(p.resolved) for p in view.predicates],
        )
        assert isinstance(view.params, ExistenceParams)
        outcome = compare(
            positions,
            view.predicates,
            crossing,
            view.params,
            reference=view.reference,
            overlap=bool(crossing.overlapping()) and view.overlap,
            k=view.disclosure,
        )
        found.append(_result(view, outcome, written))
    return found


def _survived_by_evaluator(view: CheckedView, positions: Sequence[CohortAt]) -> survival.Outcome:
    assert isinstance(view.params, SurvivalParams)
    [endpoint] = view.endpoints
    found = [ordered_inputs(listed(c.resolved, endpoint.variables)) for c in view.cohorts]
    together = shared(found)
    assert not together or view.overlap, "cohorts that share units are refused"
    return survival.survive(
        positions,
        [survival.endpoint_rows(endpoint, one) for one in found],
        view.params,
        reference=view.reference,
        overlap=bool(together),
        computation=view.identity.computation_id,
    )


def _coxed_by_evaluator(view: CheckedView, positions: Sequence[CohortAt]) -> cox.Outcome:
    assert isinstance(view.params, CoxParams)
    [endpoint] = view.endpoints
    resolved = [variable.resolved for variable in view.variables]
    read = cox.listed_variables(resolved, endpoint)
    found = [ordered_inputs(listed(c.resolved, read)) for c in view.cohorts]
    together = shared(found)
    assert not together or view.overlap, "cohorts that share units are refused"
    return cox.analyse(
        positions,
        found,
        endpoint,
        resolved,
        view.params,
        reference=view.reference,
        overlap=bool(together),
    )


def _packed_by_evaluator(
    view: CheckedView, positions: Sequence[CohortAt], analyses: Analyses
) -> packs.Outcome:
    """A view of a pack's analysis run on its inputs listed by the reference evaluator, as
    ``run_analysis`` lists them by SQL (D342, D352)."""
    assert isinstance(view.params, PackParams)
    variables = [variable.resolved for variable in view.variables]
    variables += [variable for endpoint in view.endpoints for variable in endpoint.variables]
    found = [ordered_inputs(listed(cohort.resolved, variables)) for cohort in view.cohorts]
    together = shared(found)
    independent = view.analysis.entry.fields.assumes_independent_groups
    assert not (together and independent) or view.overlap, "cohorts that share units are refused"
    analysis, _, returns = analyses.implementation(view.analysis.id)
    return packs.run_pack(
        analysis,
        returns,
        positions,
        list(zip(view.roles, view.variables, strict=True)),
        found,
        view.params,
        reference=view.reference,
        overlapping=bool(together),
        computation=view.identity.computation_id,
        endpoints=list(zip(view.endpoint_roles, view.endpoints, strict=True)),
    )


def _result(view: CheckedView, outcome: AnyOutcome, written: Mapping[str, Any]) -> Analysed:
    result = envelope(
        view,
        outcome,
        issuance="iss:01J0000000000000000000000A",
        written=dict(written),
        params={},
        engine=ENGINE,
    )
    return Analysed(view, outcome, result)


def materialise_by_evaluator(
    view: CheckedView,
) -> list[tuple[tuple[Materialised, ...], Joint | None]]:
    """A view's variables materialised over its cohorts by the reference evaluator, as
    ``run_analysis`` reads them by SQL (D327)."""
    values = [evaluate_variable(variable.resolved) for variable in view.variables]
    found: list[tuple[tuple[Materialised, ...], Joint | None]] = []
    for cohort in view.cohorts:
        truth = evaluate(cohort.resolved).values
        members = [row for row, value in enumerate(truth) if value.is_true]
        together = joint(values, members) if len(values) > 1 else None
        found.append(
            (
                tuple(
                    materialise(value, members, rows=variable.resolved.kind == "rows")
                    for value, variable in zip(values, view.variables, strict=True)
                ),
                together,
            )
        )
    return found


def shared_by_evaluator(view: CheckedView) -> bool:
    """Whether a view's cohorts share units, by the reference evaluator."""
    held = [set(evaluate(cohort.resolved).members) for cohort in view.cohorts]
    return any(held[a] & held[b] for a in range(len(held)) for b in range(a + 1, len(held)))


def _compared_by_evaluator(view: CheckedView, positions: Sequence[CohortAt]) -> columns.Outcome:
    assert isinstance(view.params, ColumnsParams)
    return columns.compare_columns(
        positions,
        view.variables,
        materialise_by_evaluator(view),
        view.params,
        reference=view.reference,
        overlap=shared_by_evaluator(view) and view.overlap,
        k=view.disclosure,
        computation=view.identity.computation_id,
    )


def _summarised_by_evaluator(
    view: CheckedView, positions: Sequence[CohortAt]
) -> distribution.Outcome:
    assert isinstance(view.params, DistributionParams)
    return distribution.summarise(
        positions, view.variables, materialise_by_evaluator(view), view.params, k=view.disclosure
    )


_REASONS = {"I": Reason.NO_INFORMATION, "A": Reason.NOT_ASSESSED, "C": Reason.NOT_COVERED}


def truth_values(pattern: str) -> list[TruthValue]:
    found: list[TruthValue] = []
    for character in pattern:
        if character == "T":
            found.append(TruthValue(Truth.TRUE))
        elif character == "F":
            found.append(TruthValue(Truth.FALSE))
        else:
            found.append(TruthValue(Truth.UNKNOWN, frozenset({_REASONS[character]})))
    return found


def _accounting(values: Sequence[TruthValue]) -> Accounting:
    unknown = [value for value in values if value.is_unknown]
    return Accounting(
        n_true=sum(value.is_true for value in values),
        n_false=sum(value.is_false for value in values),
        n_unknown=len(unknown),
        unknown_by_reason={
            reason: sum(reason in value.reasons for value in unknown) for reason in Reason
        },
        unknown_by_clause=(len(unknown),),
        lift_differs=0,
        marks=frozenset(),
    )


PATTERN_DOCUMENT: dict[str, Any] = {
    "aibi": "1",
    "dataset": "d",
    "unit": "customers",
    "cohorts": {
        name: {"all": [{"kind": "value", "column": "customers.age", "range": {"gte": age}}]}
        for name, age in (("a", 20), ("b", 30), ("c", 40), ("d", 50), ("e", 60), ("f", 65))
    },
}


def _pattern_document(
    cohorts: int, predicates: int, reference: int, overlap: bool
) -> dict[str, Any]:
    names = list(PATTERN_DOCUMENT["cohorts"])[:cohorts]
    predicate_clauses = [
        {"kind": "value", "column": "customers.age", "range": {"lt": 70 - index}}
        for index in range(predicates)
    ]
    return {
        **PATTERN_DOCUMENT,
        "views": [
            {
                "analysis": "compare.existence",
                "cohorts": names,
                "reference": names[reference],
                "params": {"predicates": predicate_clauses},
                **({"overlap": "allow"} if overlap else {}),
            }
        ],
    }


def pattern_view(
    cohorts: int,
    predicates: int,
    *,
    k: int | None = None,
    reference: int = 0,
    overlap: bool = False,
) -> CheckedView:
    """A checked view of ``cohorts`` cohorts and ``predicates`` predicates of the pattern
    document, whose truth values ``compare_patterns`` gives."""
    written = _pattern_document(cohorts, predicates, reference, overlap)
    release = shop_release({"customers": [{"customer_id": "c1", "age": 30}]})
    checked = check_document(written, release, floor=k)
    assert checked.refusals == [], checked.refusals
    [view] = checked.views
    return view


def compare_patterns(
    view: CheckedView,
    cohorts: Sequence[str],
    predicates: Sequence[str],
    *,
    k: int | None = None,
    lifts: Sequence[str] | None = None,
) -> Outcome:
    """``compare.existence`` over ``view`` with the cohorts' and predicates' truth values given
    as patterns (module docstring), under ``k``."""
    positions = [
        CohortAt(cohort, _accounting(truth_values(pattern)))
        for cohort, pattern in zip(view.cohorts, cohorts, strict=True)
    ]
    crossing = cross(
        [TruthValues.of(truth_values(pattern)) for pattern in cohorts],
        [TruthValues.of(truth_values(pattern)) for pattern in predicates],
        [None] * len(predicates)
        if lifts is None
        else [TruthValues.of(truth_values(lift)) for lift in lifts],
    )
    assert isinstance(view.params, ExistenceParams)
    return compare(
        positions,
        view.predicates,
        crossing,
        view.params,
        reference=view.reference,
        overlap=bool(crossing.overlapping()) and view.overlap,
        k=k,
    )


def compare_crossing(
    view: CheckedView, cohorts: Sequence[str], crossing: Crossing, *, k: int | None = None
) -> Outcome:
    """``compare.existence`` over ``view`` with its cohorts' truth values given as patterns and
    their crossing with its predicates given as counts, under ``k``."""
    positions = [
        CohortAt(cohort, _accounting(truth_values(pattern)))
        for cohort, pattern in zip(view.cohorts, cohorts, strict=True)
    ]
    assert isinstance(view.params, ExistenceParams)
    return compare(
        positions,
        view.predicates,
        crossing,
        view.params,
        reference=view.reference,
        overlap=bool(crossing.overlapping()) and view.overlap,
        k=k,
    )


def patterned_run(
    cohorts: Sequence[str],
    predicates: Sequence[str],
    *,
    k: int | None = None,
    reference: int = 0,
    overlap: bool = False,
    lifts: Sequence[str] | None = None,
) -> Analysed:
    """``compare.existence`` over cohorts and predicates whose truth values are ``cohorts`` and
    ``predicates`` (module docstring), under ``k``; ``lifts`` gives each predicate's truth
    values under the other lift rule."""
    view = pattern_view(len(cohorts), len(predicates), k=k, reference=reference, overlap=overlap)
    outcome = compare_patterns(view, cohorts, predicates, k=k, lifts=lifts)
    result = envelope(
        view,
        outcome,
        issuance="iss:01J0000000000000000000000A",
        written=_pattern_document(len(cohorts), len(predicates), reference, overlap),
        params={},
        engine=ENGINE,
    )
    return Analysed(view, outcome, result)


def distribution_view(
    columns: Sequence[Mapping[str, Any]],
    *,
    cohorts: int = 1,
    k: int | None = None,
    extras: Sequence[Descriptor] = (),
) -> CheckedView:
    """A checked ``summary.distribution`` view of ``columns`` over ``cohorts`` cohorts of the
    pattern document, over the extended shop with ``extras`` among its descriptors."""
    written = {
        **PATTERN_DOCUMENT,
        "views": [
            {
                "analysis": "summary.distribution",
                "cohorts": list(PATTERN_DOCUMENT["cohorts"])[:cohorts],
                "params": {"columns": [dict(column) for column in columns]},
            }
        ],
    }
    release = shop_release(
        {"customers": [{"customer_id": "c1", "age": 30}]}, extended=True, extras=extras
    )
    checked = check_document(written, release, floor=k)
    assert checked.refusals == [], checked.refusals
    [view] = checked.views
    return view


def summarise_materialised(
    view: CheckedView,
    sizes: Sequence[int],
    materialised: Sequence[tuple[Sequence[Materialised], Joint | None]],
    *,
    k: int | None = None,
    outside: int = 0,
    ends: float | None = None,
) -> distribution.Outcome:
    """``summary.distribution`` over ``view``, its cohorts of ``sizes`` units, all members, and
    its variables materialised over them as given, under ``k`` and by ``ends``; ``outside`` more
    units of the unit table are no member of any cohort."""
    positions = [
        CohortAt(cohort, _accounting(truth_values("T" * size + "F" * outside)))
        for cohort, size in zip(view.cohorts, sizes, strict=True)
    ]
    assert isinstance(view.params, DistributionParams)
    return distribution.summarise(
        positions, view.variables, materialised, view.params, k=k, ends=ends
    )


def columns_view(
    given: Sequence[Mapping[str, Any]],
    *,
    cohorts: int = 2,
    k: int | None = None,
    reference: int = 0,
    overlap: bool = False,
    level: float | None = None,
    extras: Sequence[Descriptor] = (),
) -> CheckedView:
    """A checked ``compare.columns`` view of ``given`` over ``cohorts`` cohorts of the pattern
    document, over the extended shop with ``extras`` among its descriptors."""
    names = list(PATTERN_DOCUMENT["cohorts"])[:cohorts]
    params: dict[str, Any] = {"columns": [dict(column) for column in given]}
    if level is not None:
        params["level"] = level
    written = {
        **PATTERN_DOCUMENT,
        "views": [
            {
                "analysis": "compare.columns",
                "cohorts": names,
                "reference": names[reference],
                "params": params,
                **({"overlap": "allow"} if overlap else {}),
            }
        ],
    }
    release = shop_release(
        {"customers": [{"customer_id": "c1", "age": 30}]}, extended=True, extras=extras
    )
    checked = check_document(written, release, floor=k)
    assert checked.refusals == [], checked.refusals
    [view] = checked.views
    return view


def compare_materialised(
    view: CheckedView,
    sizes: Sequence[int],
    materialised: Sequence[tuple[Sequence[Materialised], Joint | None]],
    *,
    k: int | None = None,
    overlap: bool = False,
    ends: float | None = None,
) -> columns.Outcome:
    """``compare.columns`` over ``view``, its cohorts of ``sizes`` units, all members, and its
    variables materialised over them as given, under ``k`` and by ``ends``; ``overlap`` says
    that its cohorts share units."""
    positions = [
        CohortAt(cohort, _accounting(truth_values("T" * size)))
        for cohort, size in zip(view.cohorts, sizes, strict=True)
    ]
    assert isinstance(view.params, ColumnsParams)
    return columns.compare_columns(
        positions,
        view.variables,
        materialised,
        view.params,
        reference=view.reference,
        overlap=overlap,
        k=k,
        computation=view.identity.computation_id,
        ends=ends,
    )


@pytest.fixture(scope="session")
def columned() -> Callable[..., CheckedView]:
    return columns_view


@pytest.fixture(scope="session")
def contrasted() -> Callable[..., columns.Outcome]:
    return compare_materialised


@pytest.fixture(scope="session")
def distributed() -> Callable[..., CheckedView]:
    return distribution_view


@pytest.fixture(scope="session")
def summarised() -> Callable[..., distribution.Outcome]:
    return summarise_materialised


@pytest.fixture(scope="session")
def materialised_by_evaluator() -> Callable[
    ..., list[tuple[tuple[Materialised, ...], Joint | None]]
]:
    return materialise_by_evaluator


@pytest.fixture(scope="session")
def shop() -> Callable[..., Release]:
    return shop_release


@pytest.fixture(scope="session")
def rows() -> Callable[..., dict[str, list[dict[str, object]]]]:
    return shop_rows


@pytest.fixture(scope="session")
def check() -> Callable[..., Checked]:
    return check_document


@pytest.fixture(scope="session")
def analyse() -> Callable[..., list[Analysed]]:
    return analyse_document


@pytest.fixture(scope="session")
def patterned() -> Callable[..., Analysed]:
    return patterned_run


@pytest.fixture(scope="session")
def viewed() -> Callable[..., CheckedView]:
    return pattern_view


@pytest.fixture(scope="session")
def compared() -> Callable[..., Outcome]:
    return compare_patterns


@pytest.fixture(scope="session")
def counted() -> Callable[..., Outcome]:
    return compare_crossing

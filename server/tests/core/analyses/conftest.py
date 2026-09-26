"""The analysis tests' world: a shop, built in memory (SPEC P8: no domain), and helpers that run a
document's views by the reference evaluator.

Customers have a tier and an age. They place orders, all recorded, through a channel; an order's
returns are recorded only for the orders a check lists (direct coverage), and never for phone
orders (a parent scope), so that a question about returns through orders is one whose two lift
rules differ (§6.5).

- ``shop`` builds the release from rows (``rows`` the default rows, ``customers`` of them).
- ``analyse`` loads a document, checks its views, canonicalises its cohorts and predicates, runs
  each by the reference evaluator (``evaluate``) and makes its result envelope as
  ``run_analysis`` does, returning each view's ``Analysed``.
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

from aibi.core.analyses import views
from aibi.core.analyses.existence import CohortAt, Outcome, compare
from aibi.core.analyses.registry import Analyses
from aibi.core.analyses.results import envelope
from aibi.core.analyses.views import CheckedView
from aibi.core.engine import build
from aibi.core.engine.canonical import Canonicalisation, canonicalise
from aibi.core.engine.data import Release
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.resolve import Label, ResolvedCohort
from aibi.core.engine.resolved import flipped
from aibi.core.engine.sql import Accounting, Crossing, TruthValues, cross
from aibi.core.engine.truth import Truth, TruthValue
from aibi.core.schema.analyses import ExistenceParams
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.loading import load_document
from aibi.core.schema.refusals import Refusal
from aibi.core.schema.results import ResultEnvelope
from aibi.core.schema.semantics import Reason

ORDERS = "rel:orders.customer"
RETURNS = "rel:returns.order"
ENGINE = "aibi test"


def shop_descriptors(*, disclosure: Mapping[str, Any] | None = None) -> list[Descriptor]:
    column, table, relationship = build.column, build.table, build.relationship
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
        column("customers.age", "integer", units="a"),
        table("orders", ["order_id"], role="event"),
        column("orders.order_id", "string"),
        column("orders.customer_id", "string"),
        column(
            "orders.channel",
            "category",
            permissible_values={"values": [{"value": v} for v in ("shop", "web", "phone")]},
        ),
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


def shop_rows(customers: int = 24) -> dict[str, list[dict[str, object]]]:
    """Customers of three tiers, every fifth tier not assessed, of ages 20 to 69; each places
    an order or two, one in four through the phone; every other order is checked for returns,
    and one in three of the checked ones has one."""
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
        rows["customers"].append({"customer_id": f"c{n}", "tier": tier, "age": 20 + (n * 7) % 50})
        for _ in range(1 + n % 2):
            order += 1
            channel = "phone" if order % 4 == 0 else ("shop", "web")[order % 2]
            rows["orders"].append(
                {"order_id": f"o{order}", "customer_id": f"c{n}", "channel": channel}
            )
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
    return build.release(shop_descriptors(**options), rows if rows is not None else shop_rows())


@dataclass(frozen=True)
class Analysed:
    """A view run by the reference evaluator, and its result envelope."""

    view: CheckedView
    outcome: Outcome
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
    analyses: Analyses | None = None,
    label: Label = 1,
) -> Checked:
    """A document's cohorts and views canonicalised, as the query tools do them."""
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
        positions=loaded.positions,
        predicates=[p for view in parsed for p in view.predicates],
    )
    checked, mixed = views.checked(loaded.document, parsed, canonical)
    deferred = views.deferred(loaded.document, loaded.positions)
    return Checked(canonical, checked, [*refused, *deferred, *canonical.refusals, *mixed])


def _lifted(cohort: ResolvedCohort) -> TruthValues | None:
    other = tuple(flipped(clause) for clause in cohort.clauses)
    if other == cohort.clauses:
        return None
    return TruthValues.of(evaluate(replace(cohort, clauses=other)).values)


def analyse_document(
    written: Mapping[str, Any], release: Release, *, floor: int | None = None
) -> list[Analysed]:
    """Each view of a document run by the reference evaluator, as ``run_analysis`` runs it by
    SQL; the document must check."""
    checked = check_document(written, release, floor=floor)
    assert checked.refusals == [], checked.refusals
    found: list[Analysed] = []
    for view in checked.views:
        positions = [CohortAt(cohort, evaluate(cohort.resolved)) for cohort in view.cohorts]
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
        result = envelope(
            view,
            outcome,
            issuance="iss:01J0000000000000000000000A",
            written=dict(written),
            params={},
            engine=ENGINE,
        )
        found.append(Analysed(view, outcome, result))
    return found


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

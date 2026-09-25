"""The engine tests' fixture: a city's food-safety records, and helpers to resolve and evaluate.

Owners own establishments, all of which are recorded. Establishments are inspected; an
inspection records violations, for the codes its checklist covers (grouped coverage, scoped by
code, with a checklist that covers every code), and only for routine and follow-up inspections
(a parent scope: a courtesy visit records none). An inspection also records appliance
temperatures, for the appliances it lists as checked (direct coverage, scoped by appliance).
Complaints are recorded only when made by phone or on the web (a record filter). Licences look
up their type's tier. Staff have undeclared coverage.

Test modules can't import one another (``--import-mode=importlib``), so the helpers are given as
fixtures: ``city`` builds a release from rows, ``run`` resolves and evaluates a document, and
``canon`` canonicalises one.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.engine.canonical import Canonicalisation, canonicalise
from aibi.core.engine.data import Release
from aibi.core.engine.evaluate import CohortResult, evaluate
from aibi.core.engine.resolve import Resolution, resolve
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.loading import load_document
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.schema.refusals import Refusal

INSPECTED = "rel:inspections.establishment"
VIOLATIONS = "rel:violations.inspection"
READINGS = "rel:readings.inspection"
COMPLAINTS = "rel:complaints.establishment"
LICENCES = "rel:licences.establishment"
LICENCE_TYPE = "rel:licences.type"
STAFF = "rel:staff.establishment"
OWNER = "rel:establishments.owner"


def city_descriptors(
    *,
    violations: Mapping[str, Any] | None = None,
    inspections: Mapping[str, Any] | None = None,
    extra: Sequence[Descriptor] = (),
    allow_row_ids: bool = True,
) -> list[Descriptor]:
    """The city's descriptors. ``violations`` and ``inspections`` replace the keyword
    arguments of those relationships' coverage descriptors."""
    column, table, relationship = build.column, build.table, build.relationship
    coverage = build.coverage
    disclosure: dict[str, Any] = {"allow_row_ids": allow_row_ids}
    if not allow_row_ids:
        disclosure["min_cell_count"] = 5
    return [
        build.dataset(disclosure=disclosure),
        table("establishments", ["establishment_id"]),
        column("establishments.establishment_id", "string", identifier=True),
        column(
            "establishments.cuisine",
            "category",
            permissible_values={"values": [{"value": v} for v in ("thai", "pizza", "bakery")]},
        ),
        column(
            "establishments.grade",
            "category",
            permissible_values={
                "values": [{"value": v} for v in ("C", "B", "A")],
                "ordered": True,
            },
            missing_codes={"pending": "NOT_ASSESSED", "exempt": "NOT_APPLICABLE"},
        ),
        column("establishments.seats", "integer", units="1"),
        column("establishments.frontage", "number", units="m"),
        column("establishments.revenue", "number"),
        column("establishments.opened", "date"),
        column("establishments.last_seen", "datetime"),
        column("establishments.tags", "list<category>", missing_codes={"?": "NOT_ASSESSED"}),
        column("establishments.name", "string"),
        column("establishments.chain", "boolean"),
        column("establishments.owner_id", "string"),
        column("establishments.notes", None),
        table("owners", ["owner_id"]),
        column("owners.owner_id", "string"),
        column("owners.region", "category"),
        relationship("establishments", ["owner_id"], "owners", role="owner"),
        coverage(OWNER, "all"),
        table("inspections", ["inspection_id"], role="event"),
        column("inspections.inspection_id", "string"),
        column("inspections.establishment_id", "string"),
        column(
            "inspections.kind",
            "category",
            permissible_values={
                "values": [{"value": v} for v in ("routine", "follow_up", "courtesy")]
            },
        ),
        column("inspections.score", "integer", units="1"),
        column("inspections.on", "date"),
        relationship("inspections", ["establishment_id"], "establishments", role="establishment"),
        coverage(INSPECTED, **(inspections if inspections is not None else {"parents": "all"})),
        table("violations", ["violation_id"], role="event"),
        column("violations.violation_id", "string"),
        column("violations.inspection_id", "string"),
        column(
            "violations.code",
            "category",
            permissible_values={"values": [{"value": v} for v in ("temp", "pest", "label")]},
        ),
        column(
            "violations.severity",
            "integer",
            units="1",
            missing_codes={"pending": "NOT_ASSESSED", "n/a": "NOT_APPLICABLE"},
        ),
        relationship("violations", ["inspection_id"], "inspections", role="inspection"),
        coverage(
            VIOLATIONS,
            **(
                violations
                if violations is not None
                else {
                    "parents": {
                        "assignment": {
                            "table": "inspection_checklists",
                            "parent_columns": {"inspection_id": "inspection_id"},
                            "group_column": "checklist",
                        },
                        "groups": {
                            "table": "checklist_items",
                            "group_column": "checklist",
                            "scope_columns": {"code": "code"},
                            "covers_all_column": "all_codes",
                        },
                    },
                    "parent_scope": {
                        "kind": "value",
                        "column": "inspections.kind",
                        "values": ["routine", "follow_up"],
                    },
                }
            ),
        ),
        table("inspection_checklists", ["inspection_id"], role="coverage"),
        column("inspection_checklists.inspection_id", "string"),
        column("inspection_checklists.checklist", "string"),
        table("checklist_items", ["checklist", "code"], role="coverage"),
        column("checklist_items.checklist", "string"),
        column("checklist_items.code", "string"),
        column("checklist_items.all_codes", "boolean"),
        table("readings", ["reading_id"], role="measurement"),
        column("readings.reading_id", "string"),
        column("readings.inspection_id", "string"),
        column("readings.appliance", "category"),
        column("readings.celsius", "number", units="Cel"),
        relationship("readings", ["inspection_id"], "inspections", role="inspection"),
        coverage(
            READINGS,
            {
                "table": "checked_appliances",
                "parent_columns": {"inspection_id": "inspection_id"},
                "scope_columns": {"appliance": "appliance"},
            },
        ),
        table("checked_appliances", ["inspection_id", "appliance"], role="coverage"),
        column("checked_appliances.inspection_id", "string"),
        column("checked_appliances.appliance", "string"),
        table("complaints", ["complaint_id"], role="event"),
        column("complaints.complaint_id", "string"),
        column("complaints.establishment_id", "string"),
        column("complaints.channel", "category"),
        column("complaints.severity", "integer", units="1"),
        relationship("complaints", ["establishment_id"], "establishments", role="establishment"),
        coverage(COMPLAINTS, "all", record_filter={"channel": ["phone", "web"]}),
        table("licence_types", ["type_id"]),
        column("licence_types.type_id", "string"),
        column("licence_types.tier", "integer", units="1"),
        table("licences", ["licence_id"], role="link"),
        column("licences.licence_id", "string"),
        column("licences.establishment_id", "string"),
        column("licences.type_id", "string"),
        relationship("licences", ["establishment_id"], "establishments", role="establishment"),
        relationship("licences", ["type_id"], "licence_types", role="type"),
        coverage(LICENCES, "all"),
        table("staff", ["staff_id"]),
        column("staff.staff_id", "string"),
        column("staff.establishment_id", "string"),
        column("staff.certified", "boolean"),
        relationship("staff", ["establishment_id"], "establishments", role="establishment"),
        *extra,
    ]


def city_release(
    rows: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    *,
    check: bool = True,
    **options: Any,
) -> Release:
    """A city release with these rows; ``options`` go to ``city_descriptors``. Without
    ``check``, descriptors a release would refuse are kept, for resolution to refuse."""
    return build.release(city_descriptors(**options), rows or {}, check=check)


def document(
    clauses: Sequence[Any] | Mapping[str, Sequence[Any]],
    unit: str = "establishments",
    **extra: Any,
) -> dict[str, Any]:
    """A document over dataset ``d``: one cohort ``c`` of ``clauses``, or cohorts by name."""
    cohorts = (
        {name: {"all": list(given)} for name, given in clauses.items()}
        if isinstance(clauses, Mapping)
        else {"c": {"all": list(clauses)}}
    )
    return {"aibi": "1", "dataset": "d", "unit": unit, "cohorts": cohorts, **extra}


@dataclass
class Run:
    resolution: Resolution
    results: dict[str, CohortResult]

    @property
    def refusals(self) -> list[tuple[str, str | None]]:
        return [(refusal.code, refusal.path) for refusal in self.resolution.refusals]

    def only(self) -> Refusal:
        [refusal] = self.resolution.refusals
        return refusal

    @property
    def result(self) -> CohortResult:
        [result] = self.results.values()
        return result


def run_document(written: Mapping[str, Any], releases: Release | Mapping[str, Release]) -> Run:
    """Load, resolve and evaluate a document; the loader must accept it."""
    loaded = load_document(json.dumps(written))
    assert loaded.refusals == [], loaded.refusals
    assert loaded.document is not None
    given = {"d": releases} if isinstance(releases, Release) else releases
    resolution = resolve(loaded.document, given, loaded.positions)
    results = {name: evaluate(cohort) for name, cohort in resolution.cohorts.items()}
    return Run(resolution, results)


def canonicalise_document(
    written: Mapping[str, Any],
    releases: Release | Mapping[str, Release],
    *,
    labels: Mapping[str, Any] | None = None,
    registry: PackRegistry | None = None,
    floor: int | None = None,
) -> Canonicalisation:
    """Load and canonicalise a document; the loader must accept it. Every release is ``@1``
    unless ``labels`` says otherwise."""
    loaded = load_document(json.dumps(written))
    assert loaded.refusals == [], loaded.refusals
    assert loaded.document is not None
    given = {"d": releases} if isinstance(releases, Release) else releases
    return canonicalise(
        loaded.document,
        given,
        labels=labels or {release.manifest: 1 for release in given.values()},
        registry=registry,
        floor=floor,
        positions=loaded.positions,
    )


@pytest.fixture(scope="session")
def canon() -> Callable[..., Canonicalisation]:
    return canonicalise_document


@pytest.fixture(scope="session")
def city() -> Callable[..., Release]:
    return city_release


@pytest.fixture(scope="session")
def doc() -> Callable[..., dict[str, Any]]:
    return document


@pytest.fixture(scope="session")
def run() -> Callable[..., Run]:
    return run_document

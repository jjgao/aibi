"""The engine tests' fixture: a city's food-safety records, and helpers to resolve and evaluate.

Owners own establishments, all of which are recorded. Establishments are inspected; an
inspection records violations, for the codes its checklist covers (grouped coverage, scoped by
code, with a checklist that covers every code), and only for routine and follow-up inspections
(a parent scope: a courtesy visit records none). An inspection also records appliance
temperatures, for the appliances it lists as checked (direct coverage, scoped by appliance).
Complaints are recorded only when made by phone or on the web (a record filter). Licences look
up their type's tier. Staff have undeclared coverage.

Test modules can't import one another (``--import-mode=importlib``), so the helpers are given as
fixtures: ``city`` builds a release from rows, ``run`` resolves and evaluates a document,
``canon`` canonicalises one, and ``sql`` runs a resolved cohort's SQL over its tables written as
the store writes them, in a DuckDB session of a helper process: DuckDB never runs in the test
process, as it never runs in the server's, whose memory its allocator would keep (and whose child
processes, which report their parent's resident memory as their own, would look larger).
"""

import hashlib
import json
import pickle
import subprocess
import sys
from array import array
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from hypothesis.control import currently_in_test_context

from aibi.core.engine import build
from aibi.core.engine.canonical import Canonicalisation, canonicalise
from aibi.core.engine.data import Release
from aibi.core.engine.evaluate import CohortResult, evaluate
from aibi.core.engine.resolve import Resolution, ResolvedCohort, resolve
from aibi.core.engine.sql import Accounting, CompiledCohort, compile_cohort
from aibi.core.engine.truth import TruthValue
from aibi.core.engine.worker import Rows
from aibi.core.schema.descriptors import Descriptor, TableDescriptor
from aibi.core.schema.loading import load_document
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.schema.refusals import Refusal
from aibi.core.store import parquet, tables

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


def release_blobs(release: Release, directory: Path) -> dict[str, tables.TableSource]:
    """Each table of a release in memory written as the store writes its blob (§12.2), under its
    digest, and read back as a query reads it."""
    found: dict[str, tables.TableSource] = {}
    for descriptor in release.descriptors:
        if not isinstance(descriptor, TableDescriptor):
            continue
        columns = release.columns(descriptor.id)
        fields = {}
        for column in columns:
            found_column = release.column(descriptor.id, column)
            assert found_column is not None
            fields[column] = found_column.fields
        rows = release.rows(descriptor.id)
        cells = {
            column: tuple(rows.cell(row, column) for row in range(len(rows))) for column in columns
        }
        typed = tables.TypedTable(descriptor.id, columns, cells, len(rows), {})
        data = tables.encode(typed, fields)
        path = directory / hashlib.sha256(data).hexdigest()
        if not path.exists():
            path.write_bytes(data)
        found[descriptor.id] = tables.TableSource(str(path), frozenset(parquet.names(path)))
    return found


@dataclass
class SqlRun:
    compiled: CompiledCohort
    accounting: Accounting
    values: tuple[TruthValue, ...]


_HELPER = """
import pickle, sys
from aibi.core.engine import duck
given, answers = sys.stdin.buffer, sys.stdout.buffer
while len(header := given.read(8)) == 8:
    paths, memory, statements = pickle.loads(given.read(int.from_bytes(header, "big")))
    session = duck.Session(tuple(paths), memory)
    try:
        found = duck.run(session, [duck.Statement(*one) for one in statements])
        answer = ("value", [(result.columns, result.rows) for result in found])
    except Exception as error:
        answer = ("error", repr(error))
    data = pickle.dumps(answer)
    answers.write(len(data).to_bytes(8, "big") + data)
    answers.flush()
"""


class Sessions:
    """DuckDB sessions in a helper process, one per request, as ``duck.run`` gives them."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None

    def run(
        self, paths: Sequence[str], statements: Sequence[tuple[str, Mapping[str, object]]]
    ) -> list[tuple[int, list[tuple[int, ...]]]]:
        """Each statement's number of columns and rows."""
        if self._process is None:
            self._process = subprocess.Popen(
                [sys.executable, "-c", _HELPER], stdin=subprocess.PIPE, stdout=subprocess.PIPE
            )
        given, answers = self._process.stdin, self._process.stdout
        assert given is not None
        assert answers is not None
        request = pickle.dumps((list(paths), 1 << 30, [(q, dict(p)) for q, p in statements]))
        given.write(len(request).to_bytes(8, "big") + request)
        given.flush()
        size = int.from_bytes(answers.read(8), "big")
        kind, value = pickle.loads(answers.read(size))
        assert kind == "value", value
        return value

    def close(self) -> None:
        if self._process is not None:
            assert self._process.stdin is not None
            self._process.stdin.close()
            self._process.wait(timeout=30)
            if self._process.stdout is not None:
                self._process.stdout.close()


def packed(columns: int, rows: Sequence[Sequence[int]]) -> Rows:
    """Rows as a worker's answer packs them, each column an array of 64-bit integers."""
    found = [memoryview(array("q", [row[at] for row in rows])) for at in range(columns)]
    return Rows(found, len(rows))


def run_sql(cohort: ResolvedCohort, directory: Path, sessions: Sessions) -> SqlRun:
    """A resolved cohort's counts and values queries, run in a helper's session."""
    compiled = compile_cohort(cohort, release_blobs(cohort.release, directory))
    (_, counts), values = sessions.run(
        compiled.paths,
        [
            (compiled.counts_sql, compiled.parameters),
            (compiled.values_sql, compiled.parameters),
        ],
    )
    return SqlRun(
        compiled, compiled.accounting(counts[0]), tuple(compiled.truth_values(packed(*values)))
    )


@pytest.fixture(scope="session")
def sessions() -> Iterator[Sessions]:
    found = Sessions()
    yield found
    found.close()


@pytest.fixture(scope="session")
def blobs(tmp_path_factory: pytest.TempPathFactory) -> Callable[[Release], dict[str, Any]]:
    directory = tmp_path_factory.mktemp("blobs")
    return lambda release: release_blobs(release, directory)


@pytest.fixture(scope="session")
def sql(
    tmp_path_factory: pytest.TempPathFactory, sessions: Sessions
) -> Callable[[ResolvedCohort], SqlRun]:
    directory = tmp_path_factory.mktemp("blobs")
    return lambda cohort: run_sql(cohort, directory, sessions)


@pytest.fixture(scope="session")
def canon() -> Callable[..., Canonicalisation]:
    return canonicalise_document


@pytest.fixture(scope="session")
def city() -> Callable[..., Release]:
    return city_release


@pytest.fixture(scope="session")
def doc() -> Callable[..., dict[str, Any]]:
    return document


def checked(run: Run, directory: Path, sessions: Sessions) -> Run:
    """The run, once the SQL compiler has given every cohort the reference evaluator's truth
    value, reasons and flags for every unit, and its accounting (§13.3)."""
    for name, cohort in run.resolution.cohorts.items():
        found, expected = run_sql(cohort, directory, sessions), run.results[name]
        assert found.values == expected.values, name
        assert found.accounting == Accounting(
            expected.n_true,
            expected.n_false,
            expected.n_unknown,
            dict(expected.unknown_by_reason),
            expected.unknown_by_clause,
            expected.lift_differs,
            expected.marks,
        ), name
    return run


@pytest.fixture(scope="session")
def unchecked() -> Callable[..., Run]:
    """``run_document`` alone, for a release the SQL compiler refuses."""
    return run_document


@pytest.fixture(scope="session")
def run(tmp_path_factory: pytest.TempPathFactory, sessions: Sessions) -> Callable[..., Run]:
    """``run_document``, whose every cohort the SQL compiler must answer as the evaluator does;
    within a property test the check is left to the differential tests (``test_sql``), which
    draw their own examples."""
    directory = tmp_path_factory.mktemp("checked")

    def resolved(written: Mapping[str, Any], releases: Release | Mapping[str, Release]) -> Run:
        found = run_document(written, releases)
        return found if currently_in_test_context() else checked(found, directory, sessions)

    return resolved

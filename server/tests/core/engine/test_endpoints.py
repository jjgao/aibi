"""An endpoint's rows listed by the SQL compiler and by the reference evaluator (SPEC §5.8, §13.3;
D347, D352): over machines, each run until it fails or is stopped, from when it was installed,
and the sensors fitted to them, every member's endpoint row and reasons agree, for an endpoint
on the unit table and for one read through a lookup, as a pack's requirement without ``"on":
"unit"`` reads it."""

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aibi.core.analyses import survival
from aibi.core.engine import build
from aibi.core.engine.data import Release
from aibi.core.engine.inputs import listed, ordered
from aibi.core.engine.resolve import Resolution, ViewEndpoint, resolve
from aibi.core.schema.loading import load_document

Listing = Callable[..., Any]

EXAMPLES = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
STATES = ("failed", "stopped", "unknown", "?", None)
"""A machine's state: an event, a censoring, a permissible value neither codes, not assessed,
and empty."""
TIMES = st.one_of(
    st.none(),
    st.integers(min_value=-2, max_value=6).map(float),
    st.sampled_from([0.5, 2.25, -0.0]),
)


def descriptors(entry: Any, extras: Sequence[Any] = ()) -> list[Any]:
    column, table = build.column, build.table
    fields: dict[str, Any] = {
        "table": "machines",
        "time_column": "runtime",
        "status_column": "state",
        "event_coding": {"event": ["failed"], "censored": ["stopped"]},
    }
    if entry is not None:
        fields["entry"] = entry
    return [
        build.dataset(),
        table("machines", ["machine_id"]),
        column("machines.machine_id", "string"),
        column("machines.runtime", "time_offset", units="h"),
        column(
            "machines.state",
            "category",
            permissible_values={"values": [{"value": v} for v in ("failed", "stopped", "unknown")]},
            missing_codes={"?": "NOT_ASSESSED"},
        ),
        column("machines.installed", "time_offset", units="h"),
        column("machines.size", "integer"),
        table("sensors", ["sensor_id"]),
        column("sensors.sensor_id", "string"),
        column("sensors.machine_id", "string"),
        build.relationship("sensors", ["machine_id"], "machines", role="machine"),
        build.descriptor("endpoint", "ep:runtime", fields),
        *extras,
    ]


@st.composite
def machines(draw: st.DrawFn) -> dict[str, list[dict[str, object]]]:
    count = draw(st.integers(min_value=0, max_value=8))
    found: dict[str, list[dict[str, object]]] = {"machines": [], "sensors": []}
    for index in range(count):
        key = draw(st.sampled_from(["m", "M", "é", "m10", "m9", "𝔪"])) + str(index)
        found["machines"].append(
            {
                "machine_id": key,
                "runtime": draw(TIMES),
                "state": draw(st.sampled_from(STATES)),
                "installed": draw(TIMES),
                "size": draw(st.integers(min_value=0, max_value=3)),
            }
        )
        for fitted in range(draw(st.integers(min_value=0, max_value=2))):
            found["sensors"].append({"sensor_id": f"s{index}-{fitted}", "machine_id": key})
    if found["sensors"] and draw(st.booleans()):
        found["sensors"].append({"sensor_id": "orphan", "machine_id": "none"})
    return found


def rows_of(found: Any) -> Any:
    return found.subjects, found.excluded, found.excluded_units, found.marks


def resolved(
    release: Release, unit: str, cohorts: Mapping[str, Sequence[Any]], *, on_unit: bool
) -> Resolution:
    written = {
        "aibi": "1",
        "dataset": "d",
        "unit": unit,
        "cohorts": {name: {"all": list(clauses)} for name, clauses in cohorts.items()},
    }
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    endpoint = ViewEndpoint("0/endpoint", "d", ("views", 0, "params", "endpoint"), None, on_unit)
    return resolve(loaded.document, {"d": release}, loaded.positions, endpoints=[endpoint])


ENTRIES = [None, "at_origin", {"column": "installed"}]
SMALL = {"kind": "value", "column": "machines.size", "range": {"lt": 2}}


@EXAMPLES
@given(data=machines(), entry=st.sampled_from(ENTRIES))
def test_an_endpoint_s_rows_listed_by_the_compiler_are_those_the_evaluator_lists(
    inputs_listed: Listing, data: dict[str, list[dict[str, object]]], entry: Any
) -> None:
    release = build.release(descriptors(entry), data)
    resolution = resolved(release, "machines", {"all": [], "small": [SMALL]}, on_unit=True)
    assert resolution.refusals == [], resolution.refusals
    endpoint = resolution.endpoints["0/endpoint"]
    cohorts = list(resolution.cohorts.values())
    read = inputs_listed(cohorts, endpoint.variables)
    for cohort, one in zip(cohorts, read, strict=True):
        expected = ordered(listed(cohort, endpoint.variables))
        assert rows_of(survival.endpoint_rows(endpoint, ordered(one))) == rows_of(
            survival.endpoint_rows(endpoint, expected)
        )
        assert survival.endpoint_cells(endpoint, ordered(one)) == survival.endpoint_cells(
            endpoint, expected
        )


@EXAMPLES
@given(data=machines(), entry=st.sampled_from(ENTRIES))
def test_an_endpoint_read_through_a_lookup_lists_the_same_rows_by_either_path(
    inputs_listed: Listing, data: dict[str, list[dict[str, object]]], entry: Any
) -> None:
    release = build.release(descriptors(entry), data)
    resolution = resolved(release, "sensors", {"all": []}, on_unit=False)
    assert resolution.refusals == [], resolution.refusals
    endpoint = resolution.endpoints["0/endpoint"]
    [cohort] = resolution.cohorts.values()
    [read] = inputs_listed([cohort], endpoint.variables)
    expected = ordered(listed(cohort, endpoint.variables))
    assert survival.endpoint_cells(endpoint, ordered(read)) == survival.endpoint_cells(
        endpoint, expected
    )


def test_a_sensor_without_its_machine_is_left_out_by_the_lookup_s_reason(
    inputs_listed: Listing,
) -> None:
    data: dict[str, list[dict[str, object]]] = {
        "machines": [
            {"machine_id": "m0", "runtime": 3.0, "state": "failed", "installed": 1.0, "size": 1}
        ],
        "sensors": [
            {"sensor_id": "s0", "machine_id": "m0"},
            {"sensor_id": "s1", "machine_id": "none"},
        ],
    }
    release = build.release(descriptors({"column": "installed"}), data)
    resolution = resolved(release, "sensors", {"all": []}, on_unit=False)
    endpoint = resolution.endpoints["0/endpoint"]
    [cohort] = resolution.cohorts.values()
    found = survival.endpoint_cells(endpoint, ordered(listed(cohort, endpoint.variables)))
    assert found.subjects == ((1.0, 3.0, True), None)
    assert [sorted(reasons) for reasons in found.reasons] == [[], ["NO_PARENT"]]
    [read] = inputs_listed([cohort], endpoint.variables)
    assert survival.endpoint_cells(endpoint, ordered(read)) == found


@pytest.mark.parametrize(
    ("named", "code"),
    [("ep:runtime", "INVALID_VALUE"), (None, "MISSING_MEMBER"), ("ep:none", "UNKNOWN_DESCRIPTOR")],
)
def test_an_endpoint_a_view_must_read_on_its_unit_table_is_refused_elsewhere(
    named: str | None, code: str
) -> None:
    release = build.release(descriptors("at_origin"), {"machines": [], "sensors": []})
    written = {"aibi": "1", "dataset": "d", "unit": "sensors", "cohorts": {"all": {"all": []}}}
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    endpoint = ViewEndpoint("0/endpoint", "d", ("views", 0, "params", "endpoint"), named)
    found = resolve(loaded.document, {"d": release}, loaded.positions, endpoints=[endpoint])
    assert [(r.code, r.path) for r in found.refusals] == [(code, "/views/0/params/endpoint")]
    assert found.endpoints == {}


def refused_with(
    extras: Sequence[Any],
    fields: Mapping[str, Any],
    *,
    unit: str = "machines",
    on_unit: bool = True,
    dataset: Mapping[str, Any] | None = None,
    reference: str = "d",
) -> Resolution:
    """The endpoint ``ep:other`` over the machines, its ``fields`` replacing the runtime's,
    resolved for a view of ``unit``."""
    other = build.descriptor(
        "endpoint",
        "ep:other",
        {
            "table": "machines",
            "time_column": "runtime",
            "status_column": "state",
            "event_coding": {"event": ["failed"], "censored": ["stopped"]},
            **fields,
        },
    )
    found = descriptors("at_origin", [*extras, other])
    if dataset is not None:
        found[0] = build.dataset(**dataset)
    release = build.release(found, {"machines": [], "sensors": []})
    written = {"aibi": "1", "dataset": "d", "unit": unit, "cohorts": {"all": {"all": []}}}
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    at = ("views", 0, "params", "endpoints", "time")
    endpoint = ViewEndpoint("0/endpoint", reference, at, "ep:other", on_unit)
    return resolve(loaded.document, {"d": release}, loaded.positions, endpoints=[endpoint])


def refusals_of(found: Resolution) -> list[tuple[str, str | None]]:
    return [(refusal.code, refusal.path) for refusal in found.refusals]


AT = "/views/0/params/endpoints/time"


@pytest.mark.parametrize(
    ("extras", "fields", "code", "said"),
    [
        (
            [build.column("machines.started", "time_offset", units="d")],
            {"entry": {"column": "started"}},
            "INVALID_VALUE",
            "an entry is on the time's clock",
        ),
        (
            [
                build.column(
                    "machines.states",
                    "list<category>",
                    permissible_values={"values": [{"value": "failed"}, {"value": "stopped"}]},
                    list_syntax={"format": "delimited", "delimiter": ";"},
                )
            ],
            {"status_column": "states"},
            "INVALID_VALUE",
            "one value per row",
        ),
        (
            [build.column("machines.note", None)],
            {"status_column": "note"},
            "UNDECLARED_DATATYPE",
            "is undeclared",
        ),
    ],
)
def test_an_endpoint_whose_columns_cannot_be_read_as_its_rows_is_refused_where_it_is_read(
    extras: list[Any], fields: dict[str, Any], code: str, said: str
) -> None:
    found = refused_with(extras, fields)
    assert refusals_of(found) == [(code, AT)]
    assert said in json.dumps([part.model_dump() for part in found.refusals[0].message])
    assert found.endpoints == {}


def test_an_identifier_column_is_not_read_where_the_dataset_allows_no_row_ids() -> None:
    serial = build.column("machines.serial", "time_offset", units="h", identifier=True)
    closed = {"disclosure": {"min_cell_count": 5, "allow_row_ids": False}}
    found = refused_with([serial], {"time_column": "serial"}, dataset=closed)
    assert refusals_of(found) == [("ROW_IDS_NOT_ALLOWED", AT)]
    assert refusals_of(refused_with([serial], {}, dataset=closed)) == []
    assert refusals_of(refused_with([serial], {"time_column": "serial"})) == []


def test_an_endpoint_reached_by_two_lookups_is_ambiguous_where_it_is_read() -> None:
    spare = [
        build.column("sensors.spare_id", "string"),
        build.relationship("sensors", ["spare_id"], "machines", ["machine_id"], role="spare"),
    ]
    found = refused_with(spare, {}, unit="sensors", on_unit=False)
    assert refusals_of(found) == [("AMBIGUOUS_PATH", AT)]


def test_an_endpoint_of_a_release_or_unit_the_cohorts_refuse_adds_no_refusal() -> None:
    assert refused_with([], {}, reference="e").endpoints == {}
    assert refusals_of(refused_with([], {}, reference="e")) == []
    missing = refused_with([], {}, unit="nothing")
    assert missing.endpoints == {}
    assert all(path is None or not path.startswith("/views") for _, path in refusals_of(missing))

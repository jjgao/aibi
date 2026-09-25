"""The validation gate (SPEC §13.2, D230): each structural error stops an import, a failing
proposal is dropped with its evidence instead, and semantic gaps are reported without stopping.

The fixture is a small allotment society: plots, the members who tend them, the harvests
recorded on each plot, and the crops a plot's rotation says it grows.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.engine.data import items, missing, present
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.semantics import ObservationState
from aibi.core.store import gate
from aibi.core.store.build import BuildRefused, Built, Layout, read_report
from aibi.core.store.sources import SourceValue, TextSource, TypedSource
from aibi.core.store.store import Store
from aibi.core.store.tables import TypedTable

SECRET = "zz-hidden-7"
"""A value that must never appear in a refusal, a note or a report."""

Rows = Sequence[tuple[SourceValue, ...]]


def _columns(descriptors: Sequence[Descriptor]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for descriptor in descriptors:
        if descriptor.kind == "column":
            table, column = descriptor.id.split(".", 1)
            found.setdefault(table, []).append(column)
    return found


def _import(store: Store, descriptors: Sequence[Descriptor], rows: Mapping[str, Rows]) -> Built:
    columns = _columns(descriptors)
    sources = {table: TypedSource(tuple(columns[table]), tuple(rows[table])) for table in rows}
    layouts = {table: Layout(table, tuple((c, c) for c in columns[table])) for table in rows}
    with store.pin() as pin:
        built = store.import_release(pin, "allot", descriptors, sources, layouts)
        store.publish("allot", built.manifest.hash, "operator:ada")
    return built


def _refused(
    store: Store, descriptors: Sequence[Descriptor], rows: Mapping[str, Rows]
) -> list[Any]:
    with pytest.raises(BuildRefused) as refused:
        _import(store, descriptors, rows)
    for refusal in refused.value.refusals:
        assert SECRET not in refusal.model_dump_json()
    return [(r.code, r.path, r.message[0].model_dump()["text"]) for r in refused.value.refusals]


def _key(table: str, key: Sequence[str], status: str = "asserted", **fields: Any) -> Descriptor:
    given: dict[str, Any] = {"role": "entity", "primary_key": list(key), **fields}
    return build.descriptor("table", table, given, statuses={"primary_key": status})


def _relationship(
    child: str, column: str, parent: str, status: str = "asserted", **fields: Any
) -> Descriptor:
    given = {
        "child_table": child,
        "child_columns": [column],
        "parent_table": parent,
        "parent_columns": [column],
        "cardinality": "many-to-one",
        **fields,
    }
    return build.descriptor("relationship", f"rel:{child}.{column}", given, status=status)


def _plots(
    *, key: str = "asserted", relationship: str = "asserted", extra: Sequence[Descriptor] = ()
) -> list[Descriptor]:
    column = build.column
    return [
        build.dataset(),
        _key("plots", ["plot_id"], key),
        column("plots.plot_id", "string"),
        column("plots.size", "number"),
        build.table("harvests", ["harvest_id"], role="event"),
        column("harvests.harvest_id", "integer"),
        column("harvests.plot_id", "string"),
        column("harvests.crop", "category", missing_codes={"n/a": "NOT_APPLICABLE"}),
        column("harvests.kilos", "number"),
        _relationship("harvests", "plot_id", "plots", relationship),
        *extra,
    ]


PLOTS: Rows = (("p1", 10.0), ("p2", 20.0), ("p3", 5.0))
HARVESTS: Rows = ((1, "p1", "beans", 2.5), (2, "p1", "kale", 1.0), (3, "p2", "beans", 4.0))


def _rows(plots: Rows = PLOTS, harvests: Rows = HARVESTS, **more: Rows) -> dict[str, Rows]:
    return {"plots": plots, "harvests": harvests, **more}


def test_a_clean_release_passes_and_loads(store: Store) -> None:
    built = _import(store, _plots(), _rows())
    assert built.gate is not None
    assert built.gate.refusals == ()
    release = store.load(built.manifest.hash)
    assert release.parent("rel:harvests.plot_id", 2) == 1
    assert built.manifest.report is not None
    assert read_report(store.blobs.read(built.manifest.report)) == []


def test_an_unparseable_file_stops_the_import(store: Store) -> None:
    source: dict[str, Any] = {"kind": "file", "name": "plots", "original_name": "plots.csv"}
    source["parse"] = {
        "format": "csv",
        "delimiter": ",",
        "quote": '"',
        "header_row": 0,
        "skip_rows": 0,
        "encoding": "utf-8",
    }
    descriptors = [
        build.dataset(),
        build.table("plots", ["plot_id"], source=source),
        build.column("plots.plot_id", "string"),
    ]
    layouts = {"plots": Layout("plots", (("plot_id", "plot_id"),))}
    with store.pin() as pin, pytest.raises(BuildRefused) as refused:
        store.import_release(pin, "allot", descriptors, {"plots": TextSource(b"x,y\n1\n")}, layouts)
    assert [r.code for r in refused.value.refusals] == ["UNPARSEABLE_SOURCE"]


def test_a_null_or_repeated_declared_key_stops_the_import(store: Store) -> None:
    plots: Rows = (("p1", 1.0), (None, 2.0), ("p1", 3.0), (SECRET, 4.0), (SECRET, 5.0))
    assert _refused(store, _plots(), _rows(plots, ())) == [
        (
            "KEY_NOT_UNIQUE",
            "/1/fields/primary_key",
            "The key repeats an earlier row's in 2 rows (rows 3, 5)",
        ),
        ("KEY_NULL", "/1/fields/primary_key", "A key cell is not PRESENT in 1 row (row 2)"),
        (
            "KEY_NOT_UNIQUE",
            "/9/fields/parent_columns",
            "The parent columns are not unique: they repeat an earlier row's in 2 rows (rows 3, 5)",
        ),
    ]


def test_booleans_are_keys_apart_from_numbers() -> None:
    descriptors = [build.dataset(), _key("flags", ["flag"]), build.column("flags.flag", None)]
    cells = tuple(present(value) for value in (True, 1, False, 0.0))
    flags = TypedTable("flags", ("flag",), {"flag": cells}, 4, {})
    assert gate.check(descriptors, {"flags": flags}, mode="import").refusals == ()
    again = TypedTable("flags", ("flag",), {"flag": (*cells, present(1.0))}, 5, {})
    [refusal] = gate.check(descriptors, {"flags": again}, mode="import").refusals
    assert refusal.code == "KEY_NOT_UNIQUE"


def test_a_one_to_one_relationship_with_two_children_stops_the_import(store: Store) -> None:
    descriptors = _plots()
    descriptors[9] = _relationship("harvests", "plot_id", "plots", cardinality="one-to-one")
    assert _refused(store, descriptors, _rows()) == [
        (
            "CARDINALITY_VIOLATED",
            "/9/fields/cardinality",
            "A one-to-one relationship's parent has another child in 1 row (row 2)",
        )
    ]


def test_a_dangling_foreign_key_stops_the_import_and_a_null_one_does_not(store: Store) -> None:
    harvests = (*HARVESTS, (4, SECRET, "kale", 1.0), (5, None, "kale", 1.0))
    assert _refused(store, _plots(), _rows(harvests=harvests)) == [
        (
            "DANGLING_REFERENCE",
            "/9/fields/child_columns",
            "No parent row has the foreign key of 1 row (row 4)",
        )
    ]


def test_parent_columns_are_unique_even_under_an_undeclared_key(store: Store) -> None:
    descriptors = _plots()
    descriptors[1] = build.table("plots")
    plots = (*PLOTS, ("p1", 99.0))
    assert _refused(store, descriptors, _rows(plots)) == [
        (
            "KEY_NOT_UNIQUE",
            "/9/fields/parent_columns",
            "The parent columns are not unique: they repeat an earlier row's in 1 row (row 4)",
        )
    ]


def _tended(parents: Any, **more: Any) -> list[Descriptor]:
    """Members tend plots; coverage says which plots' tending is recorded."""
    column = build.column
    return _plots(
        extra=[
            build.table("tending", ["tending_id"], role="measurement"),
            column("tending.tending_id", "integer"),
            column("tending.plot_id", "string"),
            column("tending.crop", "category"),
            _relationship("tending", "plot_id", "plots"),
            build.table("audited", role="coverage"),
            column("audited.plot", "string"),
            column("audited.crop", "category"),
            build.table("rotations", role="coverage"),
            column("rotations.plot", "string"),
            column("rotations.rotation", "string"),
            build.table("rotation_crops", role="coverage"),
            column("rotation_crops.rotation", "string"),
            column("rotation_crops.crop", "category"),
            column("rotation_crops.everything", "boolean"),
            build.coverage("rel:tending.plot_id", parents, **more),
        ]
    )


DIRECT = {
    "table": "audited",
    "parent_columns": {"plot": "plot_id"},
    "scope_columns": {"crop": "crop"},
}
GROUPED = {
    "assignment": {
        "table": "rotations",
        "parent_columns": {"plot": "plot_id"},
        "group_column": "rotation",
    },
    "groups": {
        "table": "rotation_crops",
        "group_column": "rotation",
        "scope_columns": {"crop": "crop"},
        "covers_all_column": "everything",
    },
}
TENDING: Rows = ((1, "p1", "beans"), (2, "p2", "kale"), (3, "p3", "beans"))


def _tending_rows(**given: Rows) -> dict[str, Rows]:
    rows: dict[str, Rows] = {
        "tending": TENDING,
        "audited": (("p1", "beans"), ("p2", "kale")),
        "rotations": (("p1", "r1"), ("p2", "r2")),
        "rotation_crops": (("r1", "beans", False), ("r2", "kale", True)),
    }
    rows.update(given)
    return _rows(**rows)


def test_a_direct_coverage_naming_an_unknown_parent_stops_the_import(store: Store) -> None:
    audited = (("p1", "beans"), (SECRET, "kale"))
    assert _refused(store, _tended(DIRECT), _tending_rows(audited=audited)) == [
        (
            "COVERAGE_UNKNOWN",
            "/25/fields/parents/parent_columns",
            "No parent row has the parent named in 1 row (row 2)",
        )
    ]


def test_a_grouped_assignment_naming_an_unknown_group_stops_the_import(store: Store) -> None:
    rotations = (("p1", "r1"), ("p2", SECRET))
    assert _refused(store, _tended(GROUPED), _tending_rows(rotations=rotations)) == [
        (
            "COVERAGE_UNKNOWN",
            "/25/fields/parents/assignment/group_column",
            "The group table has no row for the group named in 1 row (row 2)",
        )
    ]


@pytest.mark.parametrize(
    ("parents", "rows", "path"),
    [
        (DIRECT, {"audited": (("p1", None),)}, "/25/fields/parents/scope_columns/crop"),
        (DIRECT, {"audited": ((None, "kale"),)}, "/25/fields/parents/parent_columns/plot"),
        (GROUPED, {"rotations": (("p1", None),)}, "/25/fields/parents/assignment/group_column"),
        (
            GROUPED,
            {"rotation_crops": (("r1", None, False),)},
            "/25/fields/parents/groups/scope_columns/crop",
        ),
        (
            GROUPED,
            {"rotation_crops": (("r1", "kale", None),)},
            "/25/fields/parents/groups/covers_all_column",
        ),
    ],
)
def test_nulls_in_the_named_columns_of_each_coverage_form_stop_the_import(
    store: Store, parents: Any, rows: dict[str, Rows], path: str
) -> None:
    found = _refused(store, _tended(parents), _tending_rows(**rows))
    assert ("COVERAGE_NULL", path) in [(code, where) for code, where, _ in found]


def test_a_scope_cell_that_is_not_assessed_stops_the_import(store: Store) -> None:
    """No release holds a listing whose scope cell is missing, which the SQL compiler reads as
    the evaluator does, as listing no whole scope tuple."""
    descriptors = [
        build.column("audited.crop", "category", missing_codes={"?": "NOT_ASSESSED"})
        if descriptor.id == "audited.crop"
        else descriptor
        for descriptor in _tended(DIRECT)
    ]
    found = _refused(store, descriptors, _tending_rows(audited=(("p1", "?"),)))
    assert ("COVERAGE_NULL", "/25/fields/parents/scope_columns/crop") in [
        (code, where) for code, where, _ in found
    ]


def test_a_value_outside_a_record_filter_or_not_applicable_stops_the_import(
    store: Store,
) -> None:
    descriptors = _plots(
        extra=[
            build.coverage("rel:harvests.plot_id", "all", record_filter={"crop": ["beans", "kale"]})
        ]
    )
    harvests = (*HARVESTS, (4, "p3", SECRET, 1.0), (5, "p3", "n/a", 1.0))
    assert _refused(store, descriptors, _rows(harvests=harvests)) == [
        (
            "NOT_APPLICABLE_IN_FILTER",
            "/10/fields/record_filter/crop",
            "A filtered column is NOT_APPLICABLE in 1 row (row 5)",
        ),
        (
            "OUTSIDE_RECORD_FILTER",
            "/10/fields/record_filter/crop",
            "A value the record filter does not list is in 1 row (row 4)",
        ),
    ]


def test_colliding_identifiers_stop_the_import(store: Store) -> None:
    descriptors = _plots(extra=[build.column("plots.size", "integer")])
    plots = tuple((*row, row[1]) for row in PLOTS)
    [(code, path, _)] = _refused(store, descriptors, _rows(plots))
    assert (code, path) == ("DUPLICATE_ENTRY", "/10/id")


def test_a_layout_that_repeats_a_column_id_stops_the_import(store: Store) -> None:
    layouts = {
        "plots": Layout("plots", (("plot_id", "a"), ("plot_id", "b"))),
    }
    descriptors = [
        build.dataset(),
        _key("plots", ["plot_id"]),
        build.column("plots.plot_id", "string"),
    ]
    sources = {"plots": TypedSource(("a", "b"), (("p1", "p1"),))}
    with store.pin() as pin, pytest.raises(BuildRefused) as refused:
        store.import_release(pin, "allot", descriptors, sources, layouts)
    assert [(r.code, r.path) for r in refused.value.refusals] == [("DUPLICATE_ENTRY", "/1")]


def test_a_pack_missing_from_packs_stops_the_import(store: Store) -> None:
    descriptors = _plots()
    descriptors[0] = build.descriptor(
        "dataset", "dataset", {"name": "Allotments", "packs": []}
    ).model_copy(update={"extensions": {"garden": {"soil": "clay"}}})
    [(code, path, _)] = _refused(store, descriptors, _rows())
    assert (code, path) == ("INVALID_VALUE", "/0/extensions/garden")


def test_a_proposed_key_is_dropped_with_its_evidence_and_so_is_a_relationship_into_it(
    store: Store,
) -> None:
    descriptors = _plots(key="proposed", relationship="proposed")
    evidence = build.descriptor(
        "table",
        "plots",
        {"role": "entity", "primary_key": ["plot_id"]},
        statuses={"primary_key": "proposed"},
    )
    curation = dict(evidence.curation)
    curation["/fields/primary_key"] = curation["/fields/primary_key"].model_copy(
        update={"evidence": "The first distinct column"}
    )
    descriptors[1] = evidence.model_copy(update={"curation": curation})
    plots = (*PLOTS, ("p1", 7.0))
    built = _import(store, descriptors, _rows(plots))
    assert built.gate is not None
    assert [(d.descriptor, d.pointer, d.code, d.count, d.rows) for d in built.gate.dropped] == [
        ("plots", "/fields/primary_key", "KEY_NOT_UNIQUE", 1, (4,)),
        ("rel:harvests.plot_id", None, "KEY_NOT_UNIQUE", 1, (4,)),
    ]
    assert built.gate.dropped[0].evidence == "The first distinct column"
    kept = {d.id: d for d in built.descriptors}
    assert "primary_key" not in kept["plots"].fields.model_fields_set
    assert "/fields/primary_key" not in kept["plots"].curation
    assert "rel:harvests.plot_id" not in kept
    notes = read_report(store.blobs.read(built.manifest.report or ""))
    assert [(n["kind"], n["subject"]) for n in notes] == [
        ("dropped", "plots"),
        ("dropped", "rel:harvests.plot_id"),
    ]
    assert "The first distinct column" in str(notes[0]["message"])
    store.load(built.manifest.hash)


@pytest.mark.parametrize(("grain", "kept"), [("proposed", False), ("asserted", True)])
def test_a_proposed_grain_goes_with_the_proposed_key_it_names(
    store: Store, grain: str, kept: bool
) -> None:
    descriptors = _plots()
    descriptors[1] = build.descriptor(
        "table",
        "plots",
        {"role": "entity", "primary_key": ["plot_id"], "grain": "One row per plot_id"},
        statuses={"primary_key": "proposed", "grain": grain},
    )
    descriptors[9] = _relationship("harvests", "plot_id", "plots", "proposed")
    built = _import(store, descriptors, _rows((*PLOTS, ("p1", 7.0))))
    assert built.gate is not None
    dropped = [(d.descriptor, d.pointer, d.message) for d in built.gate.dropped]
    assert (("plots", "/fields/grain", "The key it names was dropped") in dropped) is not kept
    plots = {d.id: d for d in built.descriptors}["plots"]
    assert ("grain" in plots.fields.model_fields_set) is kept
    assert ("/fields/grain" in plots.curation) is kept


@pytest.mark.parametrize(
    ("role", "status", "kept"),
    [("entity", "proposed", False), ("entity", "asserted", True), ("event", "proposed", True)],
)
def test_a_proposed_role_that_rests_on_the_key_goes_with_it(
    store: Store, role: str, status: str, kept: bool
) -> None:
    descriptors = _plots()
    descriptors[1] = build.descriptor(
        "table",
        "plots",
        {"role": role, "primary_key": ["plot_id"]},
        statuses={"primary_key": "proposed", "role": status},
    )
    descriptors[9] = _relationship("harvests", "plot_id", "plots", "proposed")
    built = _import(store, descriptors, _rows((*PLOTS, ("p1", 7.0))))
    assert built.gate is not None
    dropped = [(d.descriptor, d.pointer, d.message) for d in built.gate.dropped]
    assert (("plots", "/fields/role", "The key it rests on was dropped") in dropped) is not kept
    plots = {d.id: d for d in built.descriptors}["plots"]
    assert ("role" in plots.fields.model_fields_set) is kept
    assert ("/fields/role" in plots.curation) is kept


def test_a_key_cell_holding_a_list_is_refused_as_a_list() -> None:
    descriptors = [
        build.dataset(),
        _key("tags", ["tag", "n"]),
        build.column("tags.tag", None),
        build.column("tags.n", "integer"),
    ]
    cells = (present("a"), items("b", "c"), present("d"))
    numbers = (present(1), present(2), present(3))
    tags = TypedTable("tags", ("tag", "n"), {"tag": cells, "n": numbers}, 3, {})
    [refusal] = gate.check(descriptors, {"tags": tags}, mode="import").refusals
    assert (refusal.code, refusal.message[0].model_dump()["text"]) == (
        "KEY_NULL",
        "A key cell holds a list, which is no key, in 1 row (row 2)",
    )


def test_a_refusal_counts_every_row_and_names_the_first_five(store: Store) -> None:
    harvests = tuple((n, "p9", "kale", 1.0) for n in range(1, 8))
    assert _refused(store, _plots(), _rows(harvests=harvests)) == [
        (
            "DANGLING_REFERENCE",
            "/9/fields/child_columns",
            "No parent row has the foreign key of 7 rows (rows 1, 2, 3, 4, 5, …)",
        )
    ]


def test_parents_whose_columns_are_not_present_repeat_nothing(store: Store) -> None:
    descriptors = _plots()
    descriptors[1] = build.table("plots")
    plots = (*PLOTS, (None, 1.0), (None, 2.0))
    assert _import(store, descriptors, _rows(plots)).gate is not None


def test_an_endpoint_declared_in_part_is_not_checked() -> None:
    descriptors = [
        build.dataset(),
        build.table("stays", ["stay_id"], role="event"),
        build.column("stays.stay_id", "integer"),
        build.column("stays.days", "time_offset", units="d"),
        build.column("stays.outcome", "category"),
        build.descriptor(
            "endpoint",
            "ep:stay",
            {"table": "stays", "time_column": "days", "status_column": "outcome"},
        ),
    ]
    table = _table_of("stays", ("stay_id", "days", "outcome"), [(1, -1.0, "gone")])
    result = gate.check(descriptors, {"stays": table}, mode="import")
    assert (result.refusals, result.gaps) == ((), ())


def test_a_proposal_failing_twice_is_dropped_once(store: Store) -> None:
    descriptors = _plots(key="proposed", relationship="proposed")
    descriptors[9] = _relationship(
        "harvests", "plot_id", "plots", "proposed", cardinality="one-to-one"
    )
    plots = (*PLOTS, ("p1", 7.0), (None, 8.0))
    harvests = (*HARVESTS, (4, "p9", "kale", 1.0))
    built = _import(store, descriptors, _rows(plots, harvests))
    assert built.gate is not None
    assert [(d.descriptor, d.pointer) for d in built.gate.dropped] == [
        ("plots", "/fields/primary_key"),
        ("rel:harvests.plot_id", None),
    ]


def test_a_proposed_dangling_relationship_is_dropped_with_its_coverage(store: Store) -> None:
    descriptors = _plots(
        relationship="proposed",
        extra=[build.coverage("rel:harvests.plot_id", "all", status="proposed")],
    )
    harvests = (*HARVESTS, (4, "p9", "kale", 1.0))
    built = _import(store, descriptors, _rows(harvests=harvests))
    assert built.gate is not None
    assert [(d.descriptor, d.code) for d in built.gate.dropped] == [
        ("rel:harvests.plot_id", "DANGLING_REFERENCE"),
        ("cov:harvests.plot_id", "UNKNOWN_DESCRIPTOR"),
    ]
    assert {d.id for d in built.descriptors}.isdisjoint(
        {"rel:harvests.plot_id", "cov:harvests.plot_id"}
    )


def test_a_draft_change_refuses_the_same_proposed_failures(store: Store) -> None:
    descriptors = _plots(key="proposed", relationship="proposed")
    plots = (*PLOTS, ("p1", 7.0))
    built = _import(store, descriptors[:9], {"plots": plots, "harvests": HARVESTS})
    tables = {
        "plots": _typed(store, built, "plots"),
        "harvests": _typed(store, built, "harvests"),
    }
    result = gate.check(descriptors, tables, mode="change")
    assert result.dropped == ()
    assert [(r.code, r.path) for r in result.refusals] == [
        ("KEY_NOT_UNIQUE", "/1/fields/primary_key"),
        ("KEY_NOT_UNIQUE", "/9/fields/parent_columns"),
    ]
    imported = gate.check(descriptors, tables, mode="import")
    assert imported.refusals == ()
    assert len(imported.dropped) == 2


def _typed(store: Store, built: Built, table: str) -> TypedTable:
    loaded = store.load(built.manifest.hash).rows(table)
    columns = sorted({column for row in loaded.rows for column in row})
    cells = {c: tuple(loaded.cell(i, c) for i in range(len(loaded))) for c in columns}
    return TypedTable(table, tuple(columns), cells, len(loaded), {})


def test_gaps_are_reported_without_stopping_the_import(store: Store) -> None:
    descriptors = _tended(DIRECT)
    descriptors[8] = build.column("harvests.kilos", "integer")
    descriptors += [
        build.column("harvests.days", "time_offset", units="d"),
        build.column("harvests.outcome", "category"),
        build.descriptor(
            "endpoint",
            "ep:yield",
            {
                "table": "harvests",
                "time_column": "days",
                "status_column": "outcome",
                "event_coding": {"event": ["picked"], "censored": ["lost"]},
            },
        ),
    ]
    harvests: Rows = (
        (1, "p1", "beans", "3", 10.0, "picked"),
        (2, "p1", "kale", SECRET, -1.0, "picked"),
        (3, "p2", "beans", "4", 5.0, SECRET),
        (4, "p2", "kale", "5", None, SECRET),
    )
    tending = (*TENDING, (4, "p1", "kale"))
    rows = _tending_rows(tending=tending)
    rows["harvests"] = harvests
    built = _import(store, descriptors, rows)
    assert built.gate is not None
    assert [(g.kind, g.subject, g.count, g.rows) for g in built.gate.gaps] == [
        ("outside_coverage", "cov:tending.plot_id", 2, (3, 4)),
        ("invalid_endpoint_rows", "ep:yield", 2, (2, 3)),
    ]
    notes = read_report(store.blobs.read(built.manifest.report or ""))
    assert [(n["kind"], n["subject"], n.get("count"), n.get("rows")) for n in notes] == [
        ("gap", "cov:tending.plot_id", 2, [3, 4]),
        ("gap", "ep:yield", 2, [2, 3]),
        ("unparsed", "harvests.kilos", 1, [2]),
    ]
    assert SECRET not in str(notes)


def test_a_grouped_listing_covers_its_groups_scope_or_everything(store: Store) -> None:
    tending = ((1, "p1", "beans"), (2, "p1", "kale"), (3, "p2", "leeks"), (4, "p3", "beans"))
    built = _import(store, _tended(GROUPED), _tending_rows(tending=tending))
    assert built.gate is not None
    # p1 follows r1, which lists beans only; p2 follows r2, which covers every crop; p3 none.
    assert [(g.subject, g.count, g.rows) for g in built.gate.gaps] == [
        ("cov:tending.plot_id", 2, (2, 4))
    ]


def test_a_change_carries_its_base_s_report(store: Store) -> None:
    harvests = (*HARVESTS, (4, "p3", "beans", "lots"))
    built = _import(store, _plots(), _rows(harvests=harvests))
    assert read_report(store.blobs.read(built.manifest.report or ""))[0]["kind"] == "unparsed"
    changed = _plots()
    changed[1] = changed[1].model_copy(update={"label": "Allotment plots"})
    with store.pin() as pin:
        after = store.change_release(pin, built.manifest.hash, changed)
    assert after.manifest.report == built.manifest.report
    assert after.manifest.hash != built.manifest.hash


def _table_of(id: str, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> TypedTable:
    cells = {
        column: tuple(
            missing(ObservationState.UNKNOWN) if row[i] is None else present(row[i]) for row in rows
        )
        for i, column in enumerate(columns)
    }
    return TypedTable(id, tuple(columns), cells, len(rows), {})


def test_a_composite_key_with_a_part_not_present_is_null() -> None:
    descriptors = [
        build.dataset(),
        _key("beds", ["plot", "bed"]),
        build.column("beds.plot", "string"),
        build.column("beds.bed", "integer"),
    ]
    rows = [("p1", 1), ("p1", 2), ("p2", 1)]
    tables = {"beds": _table_of("beds", ("plot", "bed"), rows)}
    assert gate.check(descriptors, tables, mode="import").refusals == ()
    tables = {"beds": _table_of("beds", ("plot", "bed"), [*rows, ("p1", None), (None, 3)])}
    [refusal] = gate.check(descriptors, tables, mode="import").refusals
    assert (refusal.code, refusal.message[0].model_dump()["text"]) == (
        "KEY_NULL",
        "A key cell is not PRESENT in 2 rows (rows 4, 5)",
    )


def test_a_relationship_declared_in_part_is_refused_not_dropped(store: Store) -> None:
    descriptors = _plots()
    descriptors[9] = build.descriptor(
        "relationship",
        "rel:harvests.plot_id",
        {
            "child_table": "harvests",
            "child_columns": ["plot_id"],
            "parent_table": "plots",
            "parent_columns": ["plot_id"],
            "cardinality": "many-to-one",
        },
        status="proposed",
        statuses={"child_columns": "asserted"},
    )
    harvests = (*HARVESTS, (4, "p9", "kale", 1.0))
    assert [code for code, _, _ in _refused(store, descriptors, _rows(harvests=harvests))] == [
        "DANGLING_REFERENCE"
    ]


def test_a_dropped_relationship_keeps_its_evidence(store: Store) -> None:
    descriptors = _plots(relationship="proposed")
    relationship = descriptors[9]
    curation = {
        pointer: entry.model_copy(update={"evidence": "By containment"})
        if pointer == "/fields/child_columns"
        else entry
        for pointer, entry in relationship.curation.items()
    }
    descriptors[9] = relationship.model_copy(update={"curation": curation})
    built = _import(store, descriptors, _rows(harvests=(*HARVESTS, (4, "p9", "kale", 1.0))))
    assert built.gate is not None
    [dropped] = built.gate.dropped
    assert (dropped.descriptor, dropped.evidence) == ("rel:harvests.plot_id", "By containment")


def test_a_child_whose_scope_is_not_present_is_no_gap(store: Store) -> None:
    tending = (*TENDING, (4, "p1", None))
    built = _import(store, _tended(DIRECT), _tending_rows(tending=tending))
    assert built.gate is not None
    assert [(g.subject, g.count, g.rows) for g in built.gate.gaps] == [
        ("cov:tending.plot_id", 1, (3,))
    ]


def test_endpoint_rows_are_invalid_below_zero_or_entered_at_their_time_or_after() -> None:
    descriptors = [
        build.dataset(),
        build.table("stays", ["stay_id"], role="event"),
        build.column("stays.stay_id", "integer"),
        build.column("stays.days", "time_offset", units="d"),
        build.column("stays.entered", "time_offset", units="d"),
        build.column("stays.outcome", "category"),
        build.descriptor(
            "endpoint",
            "ep:stay",
            {
                "table": "stays",
                "time_column": "days",
                "status_column": "outcome",
                "event_coding": {"event": ["left"], "censored": ["stayed"]},
                "entry": {"column": "entered"},
            },
        ),
    ]
    rows = [
        (1, 0.0, -1.0, "left"),  # a time of zero is valid
        (2, 5.0, 5.0, "left"),  # entered at its time
        (3, 5.0, 4.0, "stayed"),
        (4, -0.5, -1.0, "left"),  # a negative time
        (5, 5.0, None, "lost"),  # an entry not PRESENT: not checked at all
        (6, 3.0, 6.0, "left"),  # entered after its time
    ]
    columns = ("stay_id", "days", "entered", "outcome")
    result = gate.check(descriptors, {"stays": _table_of("stays", columns, rows)}, mode="import")
    assert result.refusals == ()
    assert [(g.subject, g.count, g.rows) for g in result.gaps] == [("ep:stay", 3, (2, 4, 6))]

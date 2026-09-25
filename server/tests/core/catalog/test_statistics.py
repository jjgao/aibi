"""Catalogue statistics counted when a release is built (SPEC §5.2, §8.4, §12.2, D270)."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from pydantic import TypeAdapter

from aibi.core.engine.data import PRESENT, Cell, items, present
from aibi.core.schema.descriptors import ColumnFields, Descriptor
from aibi.core.schema.semantics import ObservationState
from aibi.core.store import statistics

World = Any
Orchard = Callable[..., dict[str, bytes]]


def _stats(world: World, manifest: str) -> dict[str, Any]:
    found = world.store.manifest(manifest)
    assert found.statistics is not None
    return statistics.decode(world.store.blobs.read(found.statistics))


def _counted(cells: Any, fields: ColumnFields, *, identifier: bool) -> Any:
    return statistics.column_statistics(cells, fields, identifier=identifier)


def _column(fields: dict[str, Any]) -> ColumnFields:
    return ColumnFields.model_validate(fields)


def test_a_build_counts_every_table_s_rows_and_every_column_s_states(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard(24))
    trees = _stats(world, published.manifest)["trees"]
    assert trees["n_rows"] == 24
    assert trees["columns"]["height_m"]["states"] == {
        "PRESENT": 21,
        "NOT_APPLICABLE": 0,
        "NOT_ASSESSED": 0,
        "UNKNOWN": 3,
    }
    assert set(trees["columns"]) == {"tree_id", "variety", "planted", "height_m", "tags"}


def test_key_and_foreign_key_columns_have_no_distribution(world: World, orchard: Orchard) -> None:
    published = world.publish("orchard", orchard())
    found = _stats(world, published.manifest)
    for table, column in (
        ("trees", "tree_id"),
        ("harvests", "harvest_id"),
        ("harvests", "tree_id"),
    ):
        assert found[table]["columns"][column]["distribution"] == {
            "kind": "none",
            "reason": "identifier",
        }


def test_categories_list_the_declared_values_first_then_the_others_in_canonical_order() -> None:
    fields = _column(
        {
            "datatype": "category",
            "permissible_values": {"values": [{"value": "plum"}, {"value": "fig"}]},
        }
    )
    cells = [present(v) for v in ("pear", "plum", "Apple", "pear")]
    found = _counted(cells, fields, identifier=False)
    assert found["distribution"] == {
        "kind": "categories",
        "categories": [["plum", 1], ["fig", 0], ["Apple", 1], ["pear", 2]],
        "multi_membership": False,
    }


def test_a_list_column_counts_each_row_once_under_each_of_its_values() -> None:
    fields = _column(
        {"datatype": "list<category>", "list_syntax": {"format": "delimited", "delimiter": ";"}}
    )
    cells = [
        items("old", "tall", "old"),
        items("young"),
        items(Cell(ObservationState.UNKNOWN), "tall"),
        Cell(ObservationState.NOT_ASSESSED),
    ]
    found = _counted(cells, fields, identifier=False)
    assert found["states"] == {"PRESENT": 3, "NOT_APPLICABLE": 0, "NOT_ASSESSED": 1, "UNKNOWN": 0}
    assert found["distribution"] == {
        "kind": "categories",
        "categories": [["old", 1], ["tall", 2], ["young", 1]],
        "multi_membership": True,
    }


def test_booleans_are_the_categories_false_and_true() -> None:
    cells = [present(True), present(False), present(True)]
    found = _counted(cells, _column({"datatype": "boolean"}), identifier=False)
    assert found["distribution"]["categories"] == [["false", 1], ["true", 2]]


def test_too_many_distinct_values_give_no_categories() -> None:
    cells = [present(f"v{n}") for n in range(statistics.MAX_CATEGORIES + 1)]
    found = _counted(cells, _column({"datatype": "category"}), identifier=False)
    assert found["distribution"] == {"kind": "none", "reason": "categories"}


def test_strings_and_undeclared_columns_have_no_distribution() -> None:
    cells = [present("a note")]
    for fields, reason in (({"datatype": "string"}, "text"), ({}, "undeclared")):
        found = _counted(cells, _column(fields), identifier=False)
        assert found["distribution"] == {"kind": "none", "reason": reason}


def test_a_histogram_divides_the_declared_range_into_ten_bins_with_open_ends() -> None:
    fields = _column({"datatype": "number", "range": {"min": 0, "max": 10}})
    cells = [present(v) for v in (-1, 0, 0.5, 9.99, 10, 11)]
    found = _counted(cells, fields, identifier=False)["distribution"]
    assert found["from"] == "range"
    assert found["edges"] == [float(n) for n in range(11)]
    assert found["counts"] == [1, 2, 0, 0, 0, 0, 0, 0, 0, 0, 2, 1]
    assert (found["min"], found["max"]) == (-1, 11)


def test_without_a_declared_range_a_histogram_spans_the_values() -> None:
    cells = [present(v) for v in (2, 4, 12)]
    found = _counted(cells, _column({"datatype": "integer"}), identifier=False)
    distribution = found["distribution"]
    assert distribution["from"] == "data"
    assert distribution["edges"][0] == 2
    assert distribution["edges"][-1] == 12
    assert distribution["counts"][0] == distribution["counts"][-1] == 0
    assert sum(distribution["counts"]) == 3


def test_one_value_gives_one_closed_bin() -> None:
    cells = [present(5.0), present(5.0)]
    found = _counted(cells, _column({"datatype": "number"}), identifier=False)
    assert found["distribution"]["edges"] == [5.0, 5.0]
    assert found["distribution"]["counts"] == [0, 2, 0]


def test_dates_are_binned_by_whole_days_and_written_as_dates(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard(24))
    planted = _stats(world, published.manifest)["trees"]["columns"]["planted"]["distribution"]
    assert planted["scale"] == "day"
    assert all(isinstance(edge, str) and len(edge) == 10 for edge in planted["edges"])
    assert sum(planted["counts"]) == 24


def test_datetimes_are_binned_by_microseconds_in_utc() -> None:
    cells = [
        present(datetime(2026, 1, 1, tzinfo=UTC)),
        present(datetime(2026, 1, 1, 0, 0, 0, 10, tzinfo=UTC)),
    ]
    found = _counted(cells, _column({"datatype": "datetime"}), identifier=False)
    distribution = found["distribution"]
    assert distribution["edges"][0] == "2026-01-01T00:00:00Z"
    assert distribution["edges"][-1] == "2026-01-01T00:00:00.000010Z"
    assert len(distribution["edges"]) == 11
    assert distribution["max"] == "2026-01-01T00:00:00.000010Z"


def test_a_histogram_with_neither_range_nor_value_is_empty() -> None:
    cells = [Cell(ObservationState.UNKNOWN)]
    found = _counted(cells, _column({"datatype": "number"}), identifier=False)
    assert found["distribution"] == {"kind": "none", "reason": "empty"}


def test_values_beyond_the_safe_integers_give_no_histogram() -> None:
    cells = [present(2.0**60)]
    found = _counted(cells, _column({"datatype": "number"}), identifier=False)
    assert found["distribution"] == {"kind": "none", "reason": "out_of_range"}


def test_an_identifier_column_keeps_its_states_and_loses_its_distribution() -> None:
    cells = [present(1), present(2)]
    found = _counted(cells, _column({"datatype": "integer"}), identifier=True)
    assert found["states"]["PRESENT"] == 2
    assert found["distribution"] == {"kind": "none", "reason": "identifier"}


def test_a_change_that_leaves_a_table_alone_carries_its_statistics(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard())
    before = world.store.manifest(published.manifest)
    world.curate("orchard", {"op": "set", "descriptor": "trees", "pointer": "/label", "value": "T"})
    after = world.store.manifest(world.store.latest("orchard").manifest)
    assert after.statistics == before.statistics


def test_declaring_a_column_an_identifier_recounts_its_table(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    world.curate(
        "orchard",
        {
            "op": "set",
            "descriptor": "trees.variety",
            "pointer": "/fields/identifier",
            "value": True,
        },
    )
    found = _stats(world, world.store.latest("orchard").manifest)
    assert found["trees"]["columns"]["variety"]["distribution"]["reason"] == "identifier"
    assert found["harvests"]["columns"]["grade"]["distribution"]["kind"] == "categories"


def test_declaring_a_range_bins_a_reused_table_by_it(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    world.curate(
        "orchard",
        {
            "op": "set",
            "descriptor": "trees.height_m",
            "pointer": "/fields/range",
            "value": {"min": 0, "max": 20},
        },
    )
    found = _stats(world, world.store.latest("orchard").manifest)
    height = found["trees"]["columns"]["height_m"]["distribution"]
    assert height["from"] == "range"
    assert height["edges"] == [float(2 * n) for n in range(11)]


def test_the_same_inputs_give_the_same_statistics_blob(world: World, orchard: Orchard) -> None:
    first = world.publish("orchard", orchard())
    second = world.publish("orchard_again", orchard())
    assert (
        world.store.manifest(first.manifest).statistics
        == world.store.manifest(second.manifest).statistics
    )


def test_an_identifier_s_values_are_nowhere_in_the_statistics(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard())
    found = world.store.manifest(published.manifest)
    assert found.statistics is not None
    written = world.store.blobs.read(found.statistics)
    assert b"tree1" not in written
    assert b"h1" not in written


def test_only_present_cells_are_counted_in_a_distribution() -> None:
    cells = [present("a"), Cell(ObservationState.NOT_APPLICABLE), Cell(PRESENT, "b")]
    found = _counted(cells, _column({"datatype": "category"}), identifier=False)
    assert found["distribution"]["categories"] == [["a", 1], ["b", 1]]
    assert found["states"]["NOT_APPLICABLE"] == 1


def test_a_span_of_fewer_days_than_bins_has_a_bin_a_day() -> None:
    cells = [present(date(2026, 1, 1)), present(date(2026, 1, 4))]
    found = _counted(cells, _column({"datatype": "date"}), identifier=False)["distribution"]
    assert found["edges"] == ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    assert found["counts"] == [0, 1, 0, 1, 0]


def test_a_value_beyond_the_safe_integers_gives_no_histogram_within_a_declared_range() -> None:
    fields = _column({"datatype": "number", "range": {"min": 0, "max": 10}})
    found = _counted([present(1.0), present(2.0**60)], fields, identifier=False)
    assert found["distribution"] == {"kind": "none", "reason": "out_of_range"}


def test_categories_other_than_the_declared_are_in_utf_16_order() -> None:
    cells = [present("\uff21"), present("\U0001f600"), present("b")]
    found = _counted(cells, _column({"datatype": "category"}), identifier=False)
    assert [value for value, _ in found["distribution"]["categories"]] == [
        "b",
        "\U0001f600",
        "\uff21",
    ]


def _descriptor(kind: str, id: str, fields: dict[str, Any]) -> Any:
    entry = {"status": "asserted", "by": "operator:Ada", "at": "2026-01-01T00:00:00Z"}
    pointers = ["/label", *(f"/fields/{name}" for name in fields)]
    written = {
        "kind": kind,
        "id": id,
        "version": 1,
        "label": id,
        "fields": fields,
        "curation": dict.fromkeys(pointers, entry),
    }
    return TypeAdapter(Descriptor).validate_python(written)


def test_the_columns_a_grouped_coverage_assigns_parents_by_are_identifiers() -> None:
    descriptors = [
        _descriptor(
            "coverage",
            "cov:visits.guest",
            {
                "relationship": "rel:visits.guest",
                "parents": {
                    "assignment": {
                        "table": "passes",
                        "parent_columns": {"holder": "guest_id"},
                        "group_column": "pass",
                    },
                    "groups": {
                        "table": "pass_kinds",
                        "group_column": "pass",
                        "scope_columns": {"venue": "venue"},
                    },
                },
            },
        ),
        _descriptor(
            "coverage",
            "cov:visits.venue",
            {
                "relationship": "rel:visits.venue",
                "parents": {"table": "listed", "parent_columns": {"venue_ref": "venue_id"}},
            },
        ),
    ]
    assert statistics.identifiers(descriptors) == {"passes": {"holder"}, "listed": {"venue_ref"}}


def test_declaring_permissible_values_counts_a_reused_table_again(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    values = [{"value": v} for v in ("plum", "pear", "apple", "fig")]
    world.curate(
        "orchard",
        {
            "op": "set",
            "descriptor": "trees.variety",
            "pointer": "/fields/permissible_values",
            "value": {"values": values},
        },
    )
    found = _stats(world, world.store.latest("orchard").manifest)
    assert found["trees"]["columns"]["variety"]["distribution"]["categories"] == [
        ["plum", 8],
        ["pear", 8],
        ["apple", 8],
        ["fig", 0],
    ]

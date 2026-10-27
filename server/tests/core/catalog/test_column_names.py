"""The column answer names its column (D442, refining D276): `describe_column`'s `column`, `label`
and `table` are the served descriptor's `/id`, its `/label` and the table part of its `/id`, for the
release the call pins, under any *k*.

The property is held to the labels this test wrote, not only to the answer's own `descriptor`
(which shares the builder's code), with the fixture conditions under which a wrong source shows:
labels that differ from the ids and from the columns' names, and between the pinned, latest and
draft releases; a second dataset whose columns have the same ids under the same release labels,
read first; a definition that differs from the label; a label of 4,096 code points with a bidi
control last, one of white space alone, one of bidi controls, one equal to another column's id and
one to another column's name; and D440's sampled shapes (`WILD`) as column labels. The label is
built by the one helper `TableOut`'s is (`service._named`), so D440's brute force over every code
point a `Label` may hold is this label's domain guard too; the wiring test below holds that the
column's site goes through it. A cross-tool oracle holds every column's `label`, `table` and
`column` of several datasets to `describe_dataset`'s `ColumnBrief` and the `TableOut` it sits in,
which other code builds.
"""

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError
from tests.core.catalog.test_table_names import WILD
from tests.core.catalog.world import ADA

from aibi.core.catalog import service
from aibi.core.schema.catalog import (
    ColumnDescription,
    DatasetDescription,
    DescribeColumn,
    DescribeDataset,
)
from aibi.core.schema.limits import MAX_CHANGE_EDITS, MAX_STRING
from aibi.core.schema.output import Data
from aibi.core.store import sessions

World = Any
Orchard = Callable[..., dict[str, bytes]]

LONG = "L" * (MAX_STRING - 1) + "‮"

COLUMNS = ("trees.variety", "trees.height_m", "trees.tree_id", "harvests.tree_id", "harvests.kg")

#: The labels written, by dataset and release; release 1 is the importer's (each column's name).
WRITTEN: dict[str, dict[int | str, dict[str, str]]] = {
    "orchard": {
        1: {column: column.split(".", 1)[1] for column in COLUMNS},
        2: {
            "trees.variety": LONG,
            "trees.height_m": "Height, release 2",
            "trees.tree_id": "   \t ",
            "harvests.tree_id": "trees.variety",  # another column's id
            "harvests.kg": "Weight 一, release 2",
        },
        3: {
            "trees.variety": "Variety, release 3",
            "trees.height_m": "abc⁧‮def",
            "trees.tree_id": "height_m",  # another column's name
            "harvests.tree_id": "Harvested tree, release 3",
            "harvests.kg": "Weight, release 3",
        },
        "draft": {
            "trees.variety": "Variety, draft",
            "trees.height_m": "abc⁧‮def",
            "trees.tree_id": "height_m",
            "harvests.tree_id": "Harvested tree, draft",
            "harvests.kg": "Weight, draft",
        },
    },
    "grove": {
        1: {column: column.split(".", 1)[1] for column in COLUMNS},
        2: {column: f"Grove's {column}, release 2" for column in COLUMNS},
        3: {column: f"Grove's {column}, release 3" for column in COLUMNS},
    },
}

DEFINITION = "A definition, which is not the label"


def _labels(written: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {"op": "set", "descriptor": column, "pointer": "/label", "value": label}
        for column, label in written.items()
    ]


def _world(world: World, orchard: Orchard) -> None:
    for dataset in ("orchard", "grove"):
        world.publish(dataset, orchard())
        for release in (2, 3):
            edits = _labels(WRITTEN[dataset][release])
            if dataset == "orchard" and release == 2:
                # the dataset's own k from release 2 on
                edits.append(
                    {
                        "op": "set",
                        "descriptor": "dataset",
                        "pointer": "/fields/disclosure",
                        "value": {"min_cell_count": 9, "allow_row_ids": True},
                    }
                )
            if release == 3:
                edits.append(
                    {
                        "op": "set",
                        "descriptor": "trees.variety",
                        "pointer": "/definition",
                        "value": DEFINITION,
                    }
                )
            assert world.curate(dataset, *edits) == release
    opened = world.open("orchard")
    changed = {
        column: label
        for column, label in WRITTEN["orchard"]["draft"].items()
        if label != WRITTEN["orchard"][3][column]
    }
    world.change("orchard", opened, opened.draft, *_labels(changed))


def _column(catalog: Any, **given: Any) -> ColumnDescription:
    return catalog.describe_column(DescribeColumn.model_validate(given))


def _dataset(catalog: Any, **given: Any) -> DatasetDescription:
    return catalog.describe_dataset(
        DescribeDataset.model_validate({**given, "columns_limit": 1000})
    )


def _bytes(text: str) -> bytes:
    return text.encode("utf-8", "surrogatepass")


def test_each_column_is_named_by_the_id_and_label_written_for_the_release_under_any_k(
    world: World, orchard: Orchard
) -> None:
    _world(world, orchard)
    for floor in (None, 50):
        catalog = world.catalog(floor=floor)
        # the other dataset first, under every release label the orchard has too
        for release in (1, 2, 3):
            for column, label in WRITTEN["grove"][release].items():
                found = _column(catalog, dataset="grove", release=release, column=column)
                assert (found.column, found.label.data) == (column, label), (floor, release)
        manifests = {
            label: _column(
                catalog, dataset="orchard", release=label, column="trees.variety"
            ).release.manifest
            for label in (2, 3)
        }
        assert manifests[2] != manifests[3]
        pins: list[tuple[int | str | None, int | str]] = [
            (1, 1),
            (2, 2),
            (3, 3),
            ("draft", "draft"),
            (None, 3),
            (manifests[2], 2),
        ]
        for release, pinned in pins:
            given: dict[str, Any] = {"dataset": "orchard"}
            if release is not None:
                given["release"] = release
            for column, label in WRITTEN["orchard"][pinned].items():
                found = _column(catalog, **given, column=column)
                assert found.column == column
                assert found.table == column.split(".", 1)[0]
                assert _bytes(found.label.data) == _bytes(label), (floor, release, column)
                # the typed members are the served descriptor's own
                assert found.column == found.descriptor["id"]
                assert found.label.data == found.descriptor["label"]
                # no k, the floor, or the dataset's own k (from release 2 on, and the draft's)
                own = None if pinned == 1 else 9
                assert found.disclosure.min_cell_count == (floor or own), (floor, release)
                if pinned != 1 and pinned != 2 and column == "trees.variety":
                    assert found.descriptor["definition"] == DEFINITION
                    assert found.label.data != DEFINITION


def test_a_label_is_served_whole_and_as_written(world: World, orchard: Orchard) -> None:
    _world(world, orchard)
    for floor in (None, 50):
        found = _column(
            world.catalog(floor=floor), dataset="orchard", release=2, column="trees.variety"
        )
        assert found.label.data == LONG
        assert len(found.label.data) == MAX_STRING


def _wild(world: World) -> None:
    """A dataset whose columns are labelled with D440's sampled shapes, and a second table."""
    header = ",".join(["row_id", *WILD])
    files = {
        "cells.csv": (header + "\n" + ",".join(["1"] * (len(WILD) + 1)) + "\n").encode(),
        "other.csv": b"other_id,size\no1,3\n",
    }
    world.publish("wildcols", files)
    edits = _labels({f"cells.{name}": label for name, label in WILD.items()})
    opened = world.open("wildcols")
    draft = opened.draft
    for at in range(0, len(edits), MAX_CHANGE_EDITS):
        draft = world.change("wildcols", opened, draft, *edits[at : at + MAX_CHANGE_EDITS])
    assert sessions.publish(world.store, "wildcols", opened.handle, draft, ADA).label == 2


def test_the_sampled_shapes_are_served_as_written_on_columns(world: World) -> None:
    _wild(world)
    for floor in (None, 50):
        catalog = world.catalog(floor=floor)
        for name, label in WILD.items():
            found = _column(catalog, dataset="wildcols", column=f"cells.{name}")
            assert _bytes(found.label.data) == _bytes(label), (floor, name)
            assert (found.column, found.table) == (f"cells.{name}", "cells")


def _shops(world: World) -> None:
    """A dataset with a coverage table (outside the graph) and its own k of 7."""
    files = {
        "shops.csv": b"shop_id,town\ns1,a\ns2,b\ns3,c\n",
        "listing.csv": b"entry_id,note\n"
        + b"".join(f"e{n},n{n % 3}\n".encode() for n in range(1, 41)),
    }
    world.publish("shops", files)
    world.curate(
        "shops",
        {"op": "set", "descriptor": "listing", "pointer": "/fields/role", "value": "coverage"},
        {
            "op": "set",
            "descriptor": "dataset",
            "pointer": "/fields/disclosure",
            "value": {"min_cell_count": 7, "allow_row_ids": True},
        },
    )


def test_every_column_s_name_and_table_are_describe_dataset_s(
    world: World, orchard: Orchard
) -> None:
    """The cross-tool oracle: for every column of every table (`trees.tree_id` beside
    `harvests.tree_id`, a coverage table's, the sampled shapes'), `describe_column`'s `column`,
    `label`, `table` and disclosure are those of `describe_dataset`'s `ColumnBrief` and of the
    `TableOut` it is listed in, under no floor, floors of 5 and 50 and the datasets' own k."""
    _world(world, orchard)
    _wild(world)
    _shops(world)
    world.publish("sited", orchard(sites=True))
    asked: list[tuple[str, int | str | None]] = [
        ("orchard", 1),
        ("orchard", 2),
        ("orchard", None),
        ("orchard", "draft"),
        ("grove", None),
        ("wildcols", None),
        ("shops", None),
        ("sited", None),
    ]
    coverage = 0
    for floor in (None, 5, 50):
        catalog = world.catalog(floor=floor)
        for dataset, release in asked:
            given: dict[str, Any] = {"dataset": dataset}
            if release is not None:
                given["release"] = release
            described = _dataset(catalog, **given)
            assert described.columns_next is None
            for table in described.tables:
                if table.id not in described.graph.tables:
                    coverage += 1
                for brief in table.columns:
                    found = _column(catalog, **given, column=brief.id)
                    assert found.column == brief.id
                    assert found.table == table.id
                    assert _bytes(found.label.data) == _bytes(brief.label.data), (dataset, brief.id)
                    assert found.disclosure == described.disclosure
                    assert found.release == described.release
    # the shops' listing, under each floor
    assert coverage == 3


def test_a_column_s_id_begins_with_its_table_s(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    found = _column(world.catalog(), dataset="orchard", column="trees.variety")
    assert (found.column, found.table) == ("trees.variety", "trees")
    for table in ("harvests", "tree", "trees_2"):
        with pytest.raises(ValidationError, match="begins with its table"):
            found.model_copy(update={"table": table})
    with pytest.raises(ValidationError, match="begins with its table"):
        found.model_copy(update={"column": "treesx.variety"})


def test_both_answers_name_through_the_one_helper(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wiring: `TableOut`'s and `ColumnDescription`'s id and label are `service._named`'s,
    so D440's brute force over the helper is the column label's domain guard too."""
    world.publish("orchard", orchard())
    seen: list[object] = []

    def sentinel(served: Any) -> tuple[str, Data]:
        seen.append(served["id"])
        return str(served["id"]), Data(data="SENTINEL 一")

    monkeypatch.setattr(service, "_named", sentinel)
    catalog = world.catalog()
    described = _dataset(catalog, dataset="orchard")
    assert {table.label.data for table in described.tables} == {"SENTINEL 一"}
    found = _column(catalog, dataset="orchard", column="harvests.kg")
    assert (found.column, found.label.data, found.table) == (
        "harvests.kg",
        "SENTINEL 一",
        "harvests",
    )
    assert seen == ["harvests", "trees", "harvests.kg"]

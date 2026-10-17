"""The web's datatype lists held to the server (D432; MA2 of the seam's plan round 2, minor 11 of
round 3).

The web's document seam offers a value leaf's column, an analysis view's column, and a view's
column under *k* only of the datatypes ``web/src/api/datatypes.ts`` lists (held equal to
``web/tests/fixtures/datatypes.json`` by the web's own test). This test reads that fixture from the
web's tree and holds each list to the rule of the server it states:

- ``view`` is ``distribution.SUMMARISED`` and ``view_under_k`` is ``distribution._CATEGORIES``
  (under any effective *k* a numeric column needs bin edges, D329, which the seam cannot send);
- ``leaf`` is the datatypes whose ``resolve.typed_constant`` takes every string of a generated
  corpus, less those ``left_out`` names, each with its reason;
- every word is a member of ``ColumnBrief.datatype``.

And behaviourally, on a world of CSV files whose columns are of every datatype the importer
infers (boolean, integer, number, datetime, date, string and category, and the orchard's
``list<category>``): with no floor a view column is valid exactly when its datatype is in ``view``,
under a floor of 50 exactly when it is in ``view_under_k``; a leaf of a ``leaf`` datatype takes
every string of the corpus and one of another datatype refuses some; and a category leaf of a
column with permissible values refuses another value with ``NOT_PERMISSIBLE`` (shown by the page
through ``Refusal``, decision (a)).
"""

import json
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, get_args

import pytest
from tests.core.catalog.world import World, orchard_files

from aibi.core.analyses import distribution
from aibi.core.catalog.service import Catalog
from aibi.core.engine.resolve import typed_constant
from aibi.core.schema.catalog import ColumnBrief
from aibi.core.schema.descriptors import Datatype
from aibi.core.store.store import Store

FIXTURE = Path(__file__).resolve().parents[4] / "web" / "tests" / "fixtures" / "datatypes.json"

STRINGS = (
    "",
    "a",
    "x y",
    "1",
    "-1",
    "1.5",
    "9007199254740993",
    "true",
    "false",
    "2020-02-02",
    "2020-02-02T10:00:00Z",
    "$x",
    "\u0000",
    "‮abc",
    "a" * 4096,
    "\U0001f600",
)


def _lists() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _vocabulary() -> list[str]:
    """``ColumnBrief.datatype``'s words: the descriptors' ``Datatype``."""
    assert ColumnBrief.model_fields["datatype"].annotation == Datatype | None
    return list(get_args(Datatype))


def test_the_view_lists_are_the_analysis_s_summarised_and_category_sets() -> None:
    lists = _lists()
    assert lists["view"] == sorted(distribution.SUMMARISED)
    assert lists["view_under_k"] == sorted(distribution._CATEGORIES)  # pyright: ignore[reportPrivateUsage]


def test_the_leaf_list_is_typed_constant_s_every_string_datatypes_less_those_left_out() -> None:
    lists = _lists()
    every = sorted(
        datatype
        for datatype in _vocabulary()
        if all(typed_constant(value, datatype) == value for value in STRINGS)
    )
    assert every == ["category", "list<category>", "string"]
    assert sorted(lists["left_out"]) == ["list<category>"]
    assert all(reason for reason in lists["left_out"].values())
    assert lists["leaf"] == sorted(set(every) - set(lists["left_out"]))


def test_every_word_is_a_column_datatype() -> None:
    lists = _lists()
    vocabulary = set(_vocabulary())
    assert set(_vocabulary()) == {
        "number",
        "integer",
        "string",
        "boolean",
        "category",
        "list<category>",
        "date",
        "datetime",
        "time_offset",
    }
    for word in [*lists["leaf"], *lists["view"], *lists["view_under_k"], *lists["left_out"]]:
        assert word in vocabulary


def _labs() -> dict[str, bytes]:
    rows = ["lab_id,flag,n,x,when,day,kind,note"]
    for i in range(1, 61):
        rows.append(
            f"l{i},{'true' if i % 2 else 'false'},{i % 5},{i * 0.5},"
            f"2020-01-{1 + i % 28:02d}T10:00:00Z,2020-02-{1 + i % 28:02d},{'abc'[i % 3]},note {i}"
        )
    return {"labs.csv": ("\n".join(rows) + "\n").encode()}


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "imports").mkdir()
        store = Store(root / "data")
        made = World(root, store)
        made.publish("lab", _labs())
        made.publish("orchard", orchard_files(rows=40, harvests=60))
        yield made
        store.close()


def _document(dataset: str, unit: str, view: str, leaf: tuple[str, str]) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": dataset,
        "unit": unit,
        # §7.1's literal: a value that starts with "$" is written with one more.
        "cohorts": {
            "first": {
                "all": [
                    {
                        "kind": "value",
                        "column": leaf[0],
                        "values": ["$" + leaf[1] if leaf[1].startswith("$") else leaf[1]],
                    }
                ]
            }
        },
        "views": [
            {
                "analysis": "summary.distribution",
                "cohorts": ["first"],
                "params": {"columns": [{"column": view}]},
            }
        ],
    }


def _validated(world: World, catalog: Catalog, document: Mapping[str, Any]) -> dict[str, Any]:
    out = world.tool(catalog, "validate_document", {"document": dict(document)})
    assert not isinstance(out, list)
    return json.loads(out.model_dump_json())


def _datatypes(world: World, catalog: Catalog, dataset: str) -> dict[str, str]:
    out = world.tool(catalog, "describe_dataset", {"dataset": dataset})
    assert not isinstance(out, list)
    answer = json.loads(out.model_dump_json())
    return {c["id"]: c["datatype"] for t in answer["tables"] for c in t["columns"]}


CASES = (("lab", "labs", ("labs.kind", "a")), ("orchard", "trees", ("trees.variety", "apple")))


def test_a_view_column_is_valid_exactly_where_the_lists_say_live(world: World) -> None:
    lists = _lists()
    seen: set[str] = set()
    for floor, offered in ((None, lists["view"]), (50, lists["view_under_k"])):
        catalog = world.catalog(floor=floor)
        for dataset, unit, leaf in CASES:
            for column, datatype in _datatypes(world, catalog, dataset).items():
                if not column.startswith(f"{unit}."):
                    continue
                seen.add(datatype)
                found = _validated(world, catalog, _document(dataset, unit, column, leaf))
                assert found["valid"] is (datatype in offered), (floor, column, datatype, found)
                if not found["valid"]:
                    assert {r["code"] for r in found["refusals"]} <= {
                        "NOT_SUPPORTED",
                        "AGGREGATE_REQUIRED",
                        "MISSING_MEMBER",
                    }, found["refusals"]
    assert seen >= {
        "boolean",
        "integer",
        "number",
        "datetime",
        "date",
        "string",
        "category",
        "list<category>",
    }


def test_a_leaf_takes_every_string_exactly_for_the_leaf_datatypes_live(world: World) -> None:
    lists = _lists()
    catalog = world.catalog()
    for column, datatype in _datatypes(world, catalog, "lab").items():
        verdicts = [
            _validated(world, catalog, _document("lab", "labs", "labs.kind", (column, value)))[
                "valid"
            ]
            for value in STRINGS[1:]
        ]
        if datatype in lists["leaf"]:
            assert all(verdicts), (column, datatype)
        else:
            assert not all(verdicts), (column, datatype)


def test_a_category_leaf_outside_its_permissible_values_is_not_permissible(world: World) -> None:
    allowed = {"values": [{"value": "a"}, {"value": "b"}], "ordered": False}
    pointer = "/fields/permissible_values"
    world.curate(
        "lab", {"op": "set", "descriptor": "labs.kind", "pointer": pointer, "value": allowed}
    )
    catalog = world.catalog()
    found = _validated(world, catalog, _document("lab", "labs", "labs.kind", ("labs.kind", "z")))
    assert found["valid"] is False
    assert [(r["code"], r["path"]) for r in found["refusals"]] == [
        ("NOT_PERMISSIBLE", "/cohorts/first/all/0/values/0")
    ]
    assert _validated(world, catalog, _document("lab", "labs", "labs.kind", ("labs.kind", "a")))[
        "valid"
    ]

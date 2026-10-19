"""The web's datatype lists held to the server (D432; MA2 of the seam's plan round 2, minor 11 of
round 3).

The web's document seam offers a value leaf's column, an analysis view's column, and a view's
column under *k* only of the datatypes ``web/src/api/datatypes.ts`` lists (held equal to
``web/tests/fixtures/datatypes.json`` by the web's own test). This test reads that fixture from the
web's tree and holds each list to the rule of the server it states:

- ``view`` is ``distribution.SUMMARISED`` and ``view_under_k`` is ``distribution._CATEGORIES``;
- ``leaf`` is the datatypes whose ``resolve.typed_constant`` takes every string of a generated
  corpus, less those ``left_out`` names, each with its reason;
- every word is a member of ``ColumnBrief.datatype``.

And behaviourally, on worlds of CSV files whose columns are of every datatype the importer infers
(boolean, integer, number, datetime, date, string and category), a curated ``time_offset`` (the
importer infers none), the orchard's ``list<category>`` and its child table, a chain of three
tables (sites, trees, harvests: a grandparent) and two tables that share a child (trees and
pickers, parents of harvests: a sibling), under no floor, a floor of 5 and one of 50 (each with its
effective ``min_cell_count``): a brute-force cell over every (unit, column) pair holds every view
column the seam offers (the unit's own table's, of ``view``'s datatypes, or ``view_under_k``'s under
a floor) valid at validate, count_cohort and run_analysis, and gives each pair exactly the code its
relation (own, ancestor, other), datatype and floor give (``_expected``, not a subset): the server
also takes an ANCESTOR table's column (a parent's or a grandparent's) of those datatypes (the seam
does not offer it), and gives any other table's column (a child's, a sibling's)
``AGGREGATE_REQUIRED``; a second over every (unit, leaf column) pair holds every leaf column the
seam offers valid whatever the unit, and names ``list<category>`` (``left_out``) as the one more the
server takes. A leaf of a ``leaf`` datatype takes every string of the corpus and one of another
datatype refuses some; and a category leaf of a column with permissible values refuses another
value with ``NOT_PERMISSIBLE`` (shown by the page through ``Refusal``, decision (a)).
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
from aibi.core.engine.worker import Workers
from aibi.core.schema.catalog import ColumnBrief
from aibi.core.schema.descriptors import Datatype
from aibi.core.schema.limits import QueryLimits
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
    rows = ["lab_id,flag,n,x,when,day,kind,note,elapsed"]
    for i in range(1, 61):
        rows.append(
            f"l{i},{'true' if i % 2 else 'false'},{i % 5},{i * 0.5},"
            f"2020-01-{1 + i % 28:02d}T10:00:00Z,2020-02-{1 + i % 28:02d},{'abc'[i % 3]},note {i},"
            f"{i * 1.5}"
        )
    return {"labs.csv": ("\n".join(rows) + "\n").encode()}


def _chain() -> dict[str, bytes]:
    """Sites, their trees and the trees' harvests: a grandparent chain (harvests, trees, sites)."""
    sites = ["site_id,zone,area"]
    for n in range(1, 13):
        sites.append(f"site{n},{'abc'[n % 3]},{n * 2}")
    trees = ["tree_id,site_id,variety,height_m"]
    for n in range(1, 61):
        variety = ("apple", "pear", "plum")[n % 3]
        trees.append(f"tree{n},site{1 + n % 12},{variety},{1 + n * 0.25:g}")
    crops = ["harvest_id,tree_id,grade,kg"]
    for n in range(1, 181):
        crops.append(f"h{n},tree{1 + n % 60},{'AB'[n % 2]},{10 + n % 9}")
    return {
        "sites.csv": ("\n".join(sites) + "\n").encode(),
        "trees.csv": ("\n".join(trees) + "\n").encode(),
        "harvests.csv": ("\n".join(crops) + "\n").encode(),
    }


def _shared() -> dict[str, bytes]:
    """Trees and pickers, both parents of harvests: a shared child, each the other's sibling."""
    trees = ["tree_id,variety,height_m"]
    for n in range(1, 41):
        trees.append(f"tree{n},{('apple', 'pear', 'plum')[n % 3]},{1 + n * 0.25:g}")
    pickers = ["picker_id,crew,age"]
    for n in range(1, 21):
        pickers.append(f"p{n},{'xyz'[n % 3]},{20 + n}")
    crops = ["harvest_id,tree_id,picker_id,grade,kg"]
    for n in range(1, 121):
        crops.append(f"h{n},tree{1 + n % 40},p{1 + n % 20},{'AB'[n % 2]},{10 + n % 9}")
    return {
        "trees.csv": ("\n".join(trees) + "\n").encode(),
        "pickers.csv": ("\n".join(pickers) + "\n").encode(),
        "harvests.csv": ("\n".join(crops) + "\n").encode(),
    }


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "imports").mkdir()
        store = Store(root / "data")
        made = World(root, store)
        made.publish("lab", _labs())
        # The importer infers no time_offset: a curator says it, with its units.
        made.curate(
            "lab",
            {
                "op": "set",
                "descriptor": "labs.elapsed",
                "pointer": "/fields/datatype",
                "value": "time_offset",
            },
            {"op": "set", "descriptor": "labs.elapsed", "pointer": "/fields/units", "value": "mo"},
        )
        made.publish("orchard", orchard_files(rows=40, harvests=60))
        made.publish("chain", _chain())
        made.publish("shared", _shared())
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


def _described(world: World, catalog: Catalog, dataset: str) -> dict[str, Any]:
    out = world.tool(catalog, "describe_dataset", {"dataset": dataset})
    assert not isinstance(out, list)
    return json.loads(out.model_dump_json())


def _table(column: str) -> str:
    return column.split(".", 1)[0]


def _parents(answer: dict[str, Any], unit: str) -> set[str]:
    """The tables above ``unit`` in the graph: its relationships' parents, and theirs."""
    found: set[str] = set()
    todo = [unit]
    while todo:
        child = todo.pop()
        for edge in answer["graph"]["edges"]:
            if edge["child"] == child and edge["parent"] not in found:
                found.add(edge["parent"])
                todo.append(edge["parent"])
    return found


WORKERS = Workers(QueryLimits())


def _counting(world: World, floor: int | None) -> Catalog:
    """A catalogue that also counts and runs (``World.catalog`` has no query workers)."""
    return Catalog(world.store, floor=floor, workers=WORKERS)


FLOORS = (None, 5, 50)
BASE = {
    "labs": "labs.kind",
    "trees": "trees.variety",
    "harvests": "harvests.grade",
    "sites": "sites.zone",
    "pickers": "pickers.crew",
}
"""A view column valid in every unit at every floor, for the documents that test a leaf."""
NUMERIC = ("number", "integer", "time_offset")


def _expected(floor: int | None, relation: str, datatype: str) -> tuple[tuple[str, str], ...]:
    """The refusals of a view column, as (code, last token of the path), none when it is valid, by
    the column's relation to the unit (``own``, ``ancestor`` or ``other``), its datatype and the
    floor (D432's table: ``view-codes``)."""
    if relation == "other":
        return (("AGGREGATE_REQUIRED", "column"),)
    if datatype in ("string", "date", "datetime"):
        return (("NOT_SUPPORTED", "column"),)
    if datatype == "list<category>":
        return (("AGGREGATE_REQUIRED", "column"),)
    if floor is not None and datatype in NUMERIC:
        return (("MISSING_MEMBER", "bins"),)
    return ()


def _all_valid(world: World, catalog: Catalog, document: Mapping[str, Any]) -> list[str]:
    """What stops a document that validate takes: nothing when count_cohort and run_analysis take it
    too (their 200 is the not-a-list of ``world.tool``)."""
    stopped = []
    for tool in ("count_cohort", "run_analysis"):
        if isinstance(world.tool(catalog, tool, {"document": dict(document)}), list):
            stopped.append(tool)
    return stopped


DATASETS = (
    ("lab", "labs.kind"),
    ("orchard", "trees.variety"),
    ("chain", "trees.variety"),
    ("shared", "trees.variety"),
)
"""The worlds of the brute force, each with a category column for the document's leaf."""


def _relation(unit: str, column: str, parents: set[str]) -> str:
    table = _table(column)
    return "own" if table == unit else "ancestor" if table in parents else "other"


def _everywhere(
    world: World, catalog: Catalog, document: Mapping[str, Any], where: object, tally: list[object]
) -> None:
    """A document validate takes is taken by count_cohort and run_analysis too, and the tally says
    that this was asked (a cell whose number of asks is not its number of takes did not ask)."""
    assert _all_valid(world, catalog, document) == [], where
    tally.append(where)


def test_every_view_column_the_seam_offers_is_valid_and_the_rest_is_named_live(
    world: World,
) -> None:
    """Brute force over every (unit, column) pair of the orchard, the lab (every datatype the
    importer infers, and a curated ``time_offset``), a chain of three tables (a grandparent) and
    two tables sharing a child (a sibling), under no floor, 5 and 50 (the seam's ``left.view`` and
    ``underK``). A view column the seam offers (a column of the unit's own table of ``view``'s
    datatypes, or of ``view_under_k``'s under a floor) is valid at validate, count_cohort and
    run_analysis (soundness). Every pair gets exactly the refusals ``_expected`` gives by its
    relation (``own``; ``ancestor``: a parent's or a grandparent's, taken like the unit's own;
    ``other``: a child's or a sibling's, ``AGGREGATE_REQUIRED``), datatype and floor, not a subset.
    What the server takes and the seam does not offer is the named residual, asserted exactly: an
    ancestor table's column of those datatypes."""
    lists = _lists()
    seen: set[str] = set()
    keys: set[tuple[bool, str, str]] = set()
    residual: dict[str, set[str]] = {}
    taken: list[object] = []
    asked: list[object] = []
    for floor in FLOORS:
        catalog = _counting(world, floor)
        offered_types = lists["view"] if floor is None else lists["view_under_k"]
        for dataset, leaf_column in DATASETS:
            leaf = (leaf_column, "a")
            answer = _described(world, catalog, dataset)
            assert (answer["disclosure"]["min_cell_count"] is None) is (floor is None)
            datatypes = {c["id"]: c["datatype"] for t in answer["tables"] for c in t["columns"]}
            seen |= set(datatypes.values())
            for unit in answer["graph"]["tables"]:
                parents = _parents(answer, unit)
                for column, datatype in datatypes.items():
                    where = (floor, dataset, unit, column, datatype)
                    relation = _relation(unit, column, parents)
                    document = _document(dataset, unit, column, leaf)
                    found = _validated(world, catalog, document)
                    got = (
                        ()
                        if found["valid"]
                        else tuple(
                            sorted(
                                {
                                    (r["code"], (r["path"] or "").rsplit("/", 1)[-1])
                                    for r in found["refusals"]
                                }
                            )
                        )
                    )
                    assert got == _expected(floor, relation, datatype), (where, found["refusals"])
                    keys.add((floor is None, relation, datatype))
                    # What the server takes: an own or ancestor table's column, of the lists' types.
                    takes = datatype in offered_types and relation != "other"
                    assert found["valid"] is takes, (where, found["refusals"])
                    if not found["valid"]:
                        continue
                    taken.append(where)
                    _everywhere(world, catalog, document, where, asked)
                    if relation != "own":
                        residual.setdefault(f"{floor}", set()).add(f"{dataset}:{unit}:{column}")
    assert seen >= {
        "boolean",
        "integer",
        "number",
        "datetime",
        "date",
        "string",
        "category",
        "list<category>",
        "time_offset",
    }
    # Every relation met every datatype it can (the codes above were asserted for each).
    assert {datatype for _, relation, datatype in keys if relation == "own"} == seen
    assert {relation for _, relation, _ in keys} == {"own", "ancestor", "other"}
    assert {datatype for _, relation, datatype in keys if relation == "ancestor"} >= {
        "category",
        "integer",
        "number",
        "string",
    }
    assert {datatype for _, relation, datatype in keys if relation == "other"} >= {
        "category",
        "integer",
        "number",
        "string",
    }
    for relation in ("own", "ancestor", "other"):
        assert {floor for floor, rel, _ in keys if rel == relation} == {True, False}
    # Every pair the server takes was asked of count_cohort and run_analysis, and there are many.
    assert asked == taken
    assert len(taken) >= 60
    # The residual: the ancestors' columns of view's datatypes (no floor), of view_under_k's.
    ancestors = {
        "None": {
            "orchard:harvests:trees.height_m",
            "orchard:harvests:trees.variety",
            "chain:trees:sites.area",
            "chain:trees:sites.zone",
            "chain:harvests:sites.area",
            "chain:harvests:sites.zone",
            "chain:harvests:trees.height_m",
            "chain:harvests:trees.variety",
            "shared:harvests:pickers.age",
            "shared:harvests:pickers.crew",
            "shared:harvests:trees.height_m",
            "shared:harvests:trees.variety",
        },
        "5": {
            "orchard:harvests:trees.variety",
            "chain:trees:sites.zone",
            "chain:harvests:sites.zone",
            "chain:harvests:trees.variety",
            "shared:harvests:pickers.crew",
            "shared:harvests:trees.variety",
        },
    }
    assert residual == {**ancestors, "50": ancestors["5"]}


def test_every_leaf_column_the_seam_offers_is_valid_for_every_unit_and_the_rest_is_named_live(
    world: World,
) -> None:
    """Every (unit, leaf column) pair, under each floor, with a value any string column takes: a
    leaf column the seam offers (``leaf``'s datatypes) is valid, whatever the unit (a lookup up or
    down the graph, over a grandparent or across a shared child; the server resolves the path or
    refuses it: the worlds have one). What the server takes and the seam does not offer is the
    named residual, asserted exactly: ``left_out``'s ``list<category>`` (the seam leaves
    it out: offering it is M5.3-rest's)."""
    lists = _lists()
    left: set[str] = set()
    units: set[tuple[str, str]] = set()
    due: list[object] = []
    asked: list[object] = []
    for floor in FLOORS:
        catalog = _counting(world, floor)
        for dataset, _ in DATASETS:
            answer = _described(world, catalog, dataset)
            datatypes = {c["id"]: c["datatype"] for t in answer["tables"] for c in t["columns"]}
            for unit in answer["graph"]["tables"]:
                units.add((dataset, unit))
                taken: set[str] = set()
                for column, datatype in datatypes.items():
                    document = _document(dataset, unit, BASE[unit], (column, "a"))
                    found = _validated(world, catalog, document)
                    if found["valid"]:
                        taken.add(datatype)
                        # count_cohort and run_analysis of every pair of the first two worlds, and
                        # of the others' under no floor (each is the same query path).
                        if floor is None or dataset in ("lab", "orchard"):
                            due.append((floor, dataset, unit, column))
                            _everywhere(world, catalog, document, (floor, unit, column), asked)
                    if datatype in lists["leaf"]:
                        assert found["valid"], (floor, dataset, unit, column, found["refusals"])
                assert taken == (set(lists["leaf"]) | set(lists["left_out"])) & set(
                    datatypes.values()
                )
                left |= taken - set(lists["leaf"])
    assert left == set(lists["left_out"])
    assert units >= {("chain", "sites"), ("chain", "harvests"), ("shared", "pickers")}
    assert len(asked) == len(due)
    assert len(due) >= 90


def test_all_valid_sees_a_refusal(world: World) -> None:
    """The check that count_cohort and run_analysis take what validate takes is no vacuous one: a
    catalogue with no query worker takes the document at validate and refuses it at both."""
    document = _document("orchard", "trees", "trees.variety", ("trees.variety", "apple"))
    catalog = world.catalog()
    assert _validated(world, catalog, document)["valid"] is True
    assert _all_valid(world, catalog, document) == ["count_cohort", "run_analysis"]
    assert _all_valid(world, _counting(world, None), document) == []


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

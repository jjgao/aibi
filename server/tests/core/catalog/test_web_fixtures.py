"""The datasets the web's end-to-end matrix imports for its catalogue and dataset screens (D431):
``fixtures/orchard`` is the orchard of ``world.py`` (40 trees, 60 harvests), the same files the
web's unit fixtures are answers over; ``fixtures/hostile``'s headers are hostile texts, each of
which must survive the import and reach the screens as a column's label, unchanged (the matrix
then asserts that each is shown as text, never as markup)."""

import json
from pathlib import Path

from tests.core.catalog.world import World, orchard_files

from aibi.core.schema.output import Output

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures"

HOSTILE_HEADERS = [
    "<img src=x onerror=alert(1)>",
    "\u202etxt.exe",
    "</bdi><b>bold</b><bdi>",
    "h" + "x" * 2999,
    "a\u200bb\u2060c",
    "javascript:alert(1)",
]


def test_the_orchard_fixture_is_the_world_s_orchard() -> None:
    directory = FIXTURES / "orchard"
    on_disk = {
        path.name: path.read_bytes() for path in directory.iterdir() if path.suffix == ".csv"
    }
    assert on_disk == orchard_files(rows=40, harvests=60)


def test_every_hostile_header_reaches_a_column_s_label_unchanged(world: World) -> None:
    source = FIXTURES / "hostile" / "hostile.csv"
    header = source.read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",") == HOSTILE_HEADERS
    world.publish("hostile", {"hostile.csv": source.read_bytes()})
    found = world.tool(world.catalog(), "describe_dataset", {"dataset": "hostile"})
    assert isinstance(found, Output)
    described = json.loads(found.model_dump_json())
    labels = [
        column["label"]["data"] for table in described["tables"] for column in table["columns"]
    ]
    assert sorted(labels) == sorted(HOSTILE_HEADERS)
    hits = world.tool(world.catalog(), "search_catalog", {})
    assert isinstance(hits, Output)
    assert [hit["dataset"] for hit in json.loads(hits.model_dump_json())["hits"]] == ["hostile"]

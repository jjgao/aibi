"""The exit criterion of M1 (SPEC §15), as one test: biai's example spreadsheets and a
non-biomedical dataset imported, curated through the operator CLI to confirmed keys and
relationships, and published; an MCP client finds and describes them; a release is withdrawn
without deleting a blob a live release uses; and a re-import carries curation forward.

The operator works through ``aibi``, the CLI, over HTTP (§11.2): it confirms the keys and
relationships the importer proposed, and declares, with ``aibi session put``, a relationship for
each column named like another table's key that the importer proposed none for, since a column
whose values are keys of several tables is never joined silently (D229); both datasets end with
confirmed keys and relationships. The agent is the official MCP Python SDK's client, over the
streamable HTTP transport at ``/mcp`` (§11.1). Both talk to a real server on a loopback port. The
biai examples are data here, named by nothing but the files they come in (P8): the test reads
their tables, keys and columns from what the importer proposed.
"""

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

Live = Any
FIXTURES = Path(__file__).resolve().parents[4] / "fixtures"


def _cli(live: Live, *argv: str) -> Any:
    code, out, err = live.cli("--json", *argv)
    assert code == 0, (argv, out, err)
    return json.loads(out) if out.strip() else None


def _candidates(queue: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    """(child, column, parent, parent key) for each column that no proposed relationship has as
    its child and that is named like another table's one-column key, parents in id order."""
    fields: dict[str, dict[str, Any]] = {}
    for entry in queue["fields"]:
        fields.setdefault(entry["descriptor"], {})[entry["pointer"]] = entry["value"]
    keys = {
        table: members["/fields/primary_key"]
        for table, members in fields.items()
        if len(members.get("/fields/primary_key", [])) == 1
    }
    taken = {
        (members["/fields/child_table"], members["/fields/child_columns"][0])
        for identifier, members in fields.items()
        if identifier.startswith("rel:")
    }
    found: list[tuple[str, str, str, str]] = []
    columns = [name for name in fields if "." in name and not name.startswith(("rel:", "cov:"))]
    for identifier in sorted(columns):
        table, column = identifier.split(".", 1)
        if keys.get(table) == [column] or (table, column) in taken:
            continue
        found.extend(
            (table, column, parent, column)
            for parent, key in sorted(keys.items())
            if parent != table and key == [column]
        )
    return found


def _declare_relationships(live: Live, dataset: str, queue: dict[str, Any]) -> list[str]:
    """Declare, in the open session, a relationship for each column named like another table's
    key that the importer proposed none for (it proposes none when a column's values are keys
    of several tables, D229), to the first such table in id order; each ``session put`` must
    succeed, and every such column gets its relationship."""
    declared: list[str] = []
    for child, column, parent, key in _candidates(queue):
        identifier = f"rel:{child}.{column}"
        if identifier in declared:
            continue
        descriptor = {
            "kind": "relationship",
            "id": identifier,
            "label": f"{child}.{column} to {parent}",
            "fields": {
                "child_table": child,
                "child_columns": [column],
                "parent_table": parent,
                "parent_columns": [key],
                "cardinality": "many-to-one",
            },
        }
        written = live.served.root / f"{identifier.replace(':', '_')}.json"
        written.write_text(json.dumps(descriptor))
        code, out, err = live.cli("--json", "session", "put", dataset, str(written))
        assert code == 0, (identifier, out, err)
        declared.append(identifier)
    expected = {f"rel:{child}.{column}" for child, column, _, _ in _candidates(queue)}
    assert set(declared) == expected
    return declared


def _confirm_keys_and_relationships(live: Live, dataset: str) -> tuple[list[str], list[str]]:
    """Confirm, in one session, every proposed primary key and every proposed relationship, and
    declare the relationships the importer could not propose; the tables and relationships
    confirmed."""
    queue = _cli(live, "queue", dataset)
    keys = sorted(
        {f["descriptor"] for f in queue["fields"] if f["pointer"] == "/fields/primary_key"}
    )
    relationships = sorted(
        {f["descriptor"] for f in queue["fields"] if f["kind"] == "relationship"}
    )
    assert keys
    _cli(live, "session", "open", dataset)
    for table in keys:
        _cli(live, "session", "confirm", dataset, table, "/fields/primary_key", "/fields/grain")
    for relationship in relationships:
        _cli(live, "session", "confirm", dataset, relationship)
    declared = _declare_relationships(live, dataset, queue)
    shown = {
        found: _cli(live, "show", dataset, found, "--release", "draft")["descriptor"]
        for found in declared
    }
    assert all(entry["kind"] == "relationship" for entry in shown.values())
    published = _cli(live, "session", "publish", dataset)
    assert published["label"] == 2
    return keys, sorted([*relationships, *declared])


async def _agent(url: str, *calls: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Connect as an MCP client, list the tools, and make the calls; their structured results."""
    async with (
        httpx.AsyncClient(trust_env=False, timeout=60) as http,
        streamable_http_client(f"{url}/mcp", http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        initialized = await session.initialize()
        assert initialized.instructions is not None
        listed = await session.list_tools()
        assert {tool.name for tool in listed.tools} >= {
            "search_catalog",
            "describe_dataset",
            "describe_column",
            "curation_queue",
            "propose_descriptor",
        }
        found: list[dict[str, Any]] = []
        for name, arguments in calls:
            if name == "resource":
                read_back = await session.read_resource(arguments["uri"])  # pyright: ignore[reportArgumentType]
                text = getattr(read_back.contents[0], "text", "")
                found.append(json.loads(text))
                continue
            result = await session.call_tool(name, arguments)
            assert result.structuredContent is not None
            found.append({"isError": result.isError, **result.structuredContent})
        return found


def _asserted(described: dict[str, Any], tables: list[str], relationships: list[str]) -> None:
    by_id = {t["descriptor"]["id"]: t["descriptor"] for t in described["tables"]}
    for table in tables:
        entry = by_id[table]["curation"]["/fields/primary_key"]
        assert (entry["status"], entry["by"]) == ("asserted", "operator:Ada Lovelace")
    listed = {r["id"]: r for r in described["relationships"]}
    for relationship in relationships:
        statuses = {
            e["status"] for p, e in listed[relationship]["curation"].items() if p != "/label"
        }
        assert statuses == {"asserted"}


def test_m1_datasets_are_imported_curated_found_described_withdrawn_and_reimported(
    live: Live, tmp_path: Path
) -> None:
    served = live.served
    spreadsheets = tmp_path / "examples.zip"
    with zipfile.ZipFile(spreadsheets, "w") as archive:
        for found in sorted((FIXTURES / "biai").iterdir()):
            if found.suffix != ".md":
                archive.write(found, found.name)
    library = served.imports / "library"
    shutil.copytree(FIXTURES / "library", library, ignore=shutil.ignore_patterns("formats", "*.md"))
    following = served.imports / "library_next"
    shutil.copytree(FIXTURES / "library_next", following, ignore=shutil.ignore_patterns("*.md"))

    examples = _cli(live, "import", "examples", str(spreadsheets), "--upload")
    assert examples["label"] == 1
    lending = _cli(live, "import", "library", str(library))
    assert lending["label"] == 1
    example_keys, example_relationships = _confirm_keys_and_relationships(live, "examples")
    library_keys, library_relationships = _confirm_keys_and_relationships(live, "library")
    assert example_keys
    assert example_relationships
    assert library_keys
    assert library_relationships

    searched, examples_described, library_described, column, resource = anyio.run(
        _agent,
        live.url,
        ("search_catalog", {}),
        ("describe_dataset", {"dataset": "examples"}),
        ("describe_dataset", {"dataset": "library"}),
        ("describe_column", {"dataset": "library", "column": "members.age"}),
        ("resource", {"uri": "aibi://dataset/library@2/dataset"}),
    )
    assert not searched["isError"]
    assert [(h["dataset"], h["release"]["label"]) for h in searched["hits"]] == [
        ("examples", 2),
        ("library", 2),
    ]
    for hit in searched["hits"]:
        assert all(t["rows"]["reference"].startswith("stat:sha256:") for t in hit["tables"])
    _asserted(examples_described, example_keys, example_relationships)
    _asserted(library_described, library_keys, library_relationships)
    graphed = {e["relationship"] for e in examples_described["graph"]["edges"]}
    assert set(example_relationships) <= graphed
    assert library_described["applicable_analyses"] == []
    assert column["statistics"]["states"]["PRESENT"]["count"] > 0
    assert resource["id"] == "dataset"

    live_before = served.store.manifest(served.store.latest("library").manifest)
    first = served.store.labels("library")[0].manifest
    first_manifest = served.store.manifest(first)
    shared = first_manifest.blobs() & live_before.blobs()
    assert shared
    assert _cli(live, "withdraw", "library", "1")["labels"] == [1]
    for blob in sorted(live_before.blobs()):
        assert served.store.blobs.exists(blob), blob
    assert served.store.blobs.exists(first.removeprefix("sha256:"))
    assert not served.store.blobs.exists(first_manifest.descriptors)

    reimported = _cli(live, "reimport", "library", str(following))
    assert reimported["label"] == 3
    withdrawn, described_again, found_again = anyio.run(
        _agent,
        live.url,
        ("describe_dataset", {"dataset": "library", "release": 1}),
        ("describe_dataset", {"dataset": "library"}),
        ("search_catalog", {"text": "library"}),
    )
    assert withdrawn["isError"]
    assert withdrawn["refusals"][0]["code"] == "RELEASE_WITHDRAWN"
    assert described_again["release"]["label"] == 3
    carried = [
        t for t in library_keys if t in {x["descriptor"]["id"] for x in described_again["tables"]}
    ]
    assert carried
    _asserted(described_again, carried, library_relationships)
    assert [h["release"]["label"] for h in found_again["hits"]] == [3]

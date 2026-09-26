"""The catalogue's service functions (SPEC §8.1, §8.4, §9.4, §11.1, D273–D277, D279)."""

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

import pytest
from pydantic import JsonValue, TypeAdapter

import aibi
from aibi.core.catalog import index as indexing
from aibi.core.catalog.index import entries
from aibi.core.catalog.service import NOTE_TEXT, ToolRefused
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.importers.files import FileImporter
from aibi.core.schema.catalog import (
    CatalogHits,
    ColumnDescription,
    DatasetDescription,
    DescribeColumn,
    DescribeDataset,
    Proposed,
    ProposeDescriptor,
    QueueOut,
    QueueRequest,
    SearchCatalog,
)
from aibi.core.schema.curation import ProposalInput
from aibi.core.schema.descriptors import Descriptor, ModelCardDescriptor
from aibi.core.schema.limits import MAX_ENTRIES, MAX_STRING
from aibi.core.schema.output import DataSegment, Output, text
from aibi.core.schema.pack_api import ImportNote, Pack, PackManifest, PackRegistry
from aibi.core.schema.refusals import Refusal
from aibi.core.schema.results import STAT_REFERENCE_RE
from aibi.core.store import proposals, statistics, tables
from aibi.core.store.blobs import MissingBlobError
from aibi.core.store.redaction import REDACTORS
from aibi.core.store.store import Pin, StoreRefused

World = Any
Orchard = Callable[..., dict[str, bytes]]
BAKERY = {
    "loaves.csv": b"loaf_id,flour,grams\n"
    + b"".join(f"l{n},{'rye' if n % 2 else 'wheat'},{400 + n}\n".encode() for n in range(1, 25)),
    "staff.csv": b"staff_id,shift\n"
    + b"".join(f"s{n},{'early' if n % 2 else 'late'}\n".encode() for n in range(1, 25)),
}


def _search(world: World, **given: JsonValue) -> CatalogHits:
    made: dict[str, Any] = cast(dict[str, Any], given.pop("catalog", {}))
    return world.catalog(**made).search_catalog(SearchCatalog.model_validate(given))


def _dumped(output: Output) -> dict[str, Any]:
    found: dict[str, Any] = json.loads(output.model_dump_json())
    return found


def _refusal(world: World, catalog: Any, name: str, body: JsonValue) -> Refusal:
    found = world.tool(catalog, name, body)
    assert isinstance(found, list), found
    return found[0]


def _data(segments: Any) -> list[str]:
    return [segment.data for segment in segments if isinstance(segment, DataSegment)]


def _codes(output: Any) -> list[str]:
    return [str(caveat.code) for caveat in output.caveats]


# --- search_catalog ---------------------------------------------------------------------------


def test_without_a_filter_every_published_dataset_is_found_in_dataset_order(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    world.publish("bakery", BAKERY)
    found = _search(world)
    assert [hit.dataset for hit in found.hits] == ["bakery", "orchard"]
    assert found.total == 2
    assert found.next_offset is None
    trees = next(t for t in found.hits[1].tables if t.id == "trees")
    assert trees.rows.count == 24
    assert STAT_REFERENCE_RE.fullmatch(trees.rows.reference)
    assert trees.rows.reference.endswith("/trees/n_rows")
    assert found.caveats == []


def test_every_word_of_the_text_must_occur(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    world.publish("bakery", BAKERY)
    assert [h.dataset for h in _search(world, text="TREE harvest").hits] == ["orchard"]
    assert [h.dataset for h in _search(world, text="flour").hits] == ["bakery"]
    assert _search(world, text="tree flour").hits == []


def test_roles_and_a_least_number_of_rows_filter_by_tables(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard(24, harvests=40))
    world.publish("bakery", BAKERY)
    assert [h.dataset for h in _search(world, roles=["measurement"]).hits] == ["orchard"]
    assert [h.dataset for h in _search(world, min_rows=30).hits] == ["orchard"]
    assert [h.dataset for h in _search(world, min_rows=24).hits] == ["bakery", "orchard"]


def test_completeness_counts_present_cells_against_the_table_s_rows(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard(24))
    below = {"min_present": 0.9, "datatype": "number"}
    above = {"min_present": 0.85, "datatype": "number"}
    assert _search(world, completeness=below).hits == []
    assert [h.dataset for h in _search(world, completeness=above).hits] == ["orchard"]


def test_concepts_match_only_mappings_an_operator_asserted(world: World) -> None:
    world.publish("bakery", BAKERY)
    assert _search(world, concepts=["core:person"]).hits == []
    world.curate(
        "bakery",
        {
            "op": "set",
            "descriptor": "staff",
            "pointer": "/fields/maps_to",
            "value": {"concept": "core:person"},
        },
    )
    found = _search(world, concepts=["core:person"])
    assert [h.dataset for h in found.hits] == ["bakery"]
    assert found.hits[0].concepts == ["core:person"]


def test_domain_tags_and_data_use_codes_filter_by_the_dataset_descriptor(world: World) -> None:
    world.publish("bakery", BAKERY)
    world.curate(
        "bakery",
        {"op": "set", "descriptor": "dataset", "pointer": "/fields/domain_tags", "value": ["Food"]},
        {
            "op": "set",
            "descriptor": "dataset",
            "pointer": "/fields/data_use",
            "value": [{"system": "DUO", "code": "0000042", "label": "GRU", "relation": "exact"}],
        },
    )
    assert [h.dataset for h in _search(world, domain_tags=["food"]).hits] == ["bakery"]
    assert _search(world, domain_tags=["drink"]).hits == []
    hit = _search(world, data_use=[{"system": "DUO", "code": "0000042"}]).hits[0]
    assert hit.domain_tags[0].data == "Food"
    assert hit.data_use[0].code.data == "0000042"


def test_a_pack_s_facets_are_named_by_the_pack_and_filter_the_search(
    world: World, orchard: Orchard, orchards: Callable[..., Any]
) -> None:
    registry = orchards()
    world.publish("orchard", orchard(), registry=registry, pack="orchards")
    world.publish("bakery", BAKERY)
    catalog = {"registry": registry}
    hit = _search(world, catalog=catalog, facets={"orchards.region": ["north"]}).hits
    assert [h.dataset for h in hit] == ["orchard"]
    assert {name: [v.data for v in values] for name, values in hit[0].facets.items()} == {
        "orchards.region": ["north"]
    }
    assert _search(world, catalog=catalog, facets={"orchards.region": ["south"]}).hits == []


@pytest.mark.parametrize(
    "facet",
    [
        lambda release: 1 / 0,
        lambda release: {"Not An Identifier": ["x"]},
        lambda release: {"region": "north"},
        lambda release: {"region": [3]},
        lambda release: {"region": ["x"] * (MAX_ENTRIES + 1)},
        lambda release: {f"f{n}": ["x"] for n in range(MAX_ENTRIES + 1)},
        lambda release: {"region": ["x" * (MAX_STRING + 1)]},
    ],
)
def test_a_facet_that_fails_is_left_out_and_never_raised(
    world: World, orchard: Orchard, orchards: Callable[..., Any], facet: Any
) -> None:
    registry = orchards(facet)
    world.publish("orchard", orchard(), registry=registry, pack="orchards")
    hit = _search(world, catalog={"registry": registry}).hits[0]
    assert hit.facets == {}


def test_a_search_is_read_a_page_at_a_time(world: World, orchard: Orchard) -> None:
    for dataset in ("a", "b", "c"):
        world.publish(dataset, BAKERY)
    first = _search(world, limit=2)
    assert [h.dataset for h in first.hits] == ["a", "b"]
    assert (first.total, first.next_offset) == (3, 2)
    second = _search(world, limit=2, offset=2)
    assert [h.dataset for h in second.hits] == ["c"]
    assert second.next_offset is None


def test_a_suppressed_row_count_matches_no_threshold_and_is_caveated(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard(24, harvests=40))
    found = _search(world, catalog={"floor": 30}, min_rows=20)
    tables = {t.id: t.rows for t in found.hits[0].tables}
    assert tables["trees"].count is None
    assert tables["trees"].reference.endswith("/trees/n_rows?floor=30")
    assert tables["harvests"].count == 40
    assert _codes(found) == ["SUPPRESSED"]
    assert _search(world, catalog={"floor": 30}, roles=["entity"], min_rows=41).hits == []


def test_the_index_follows_the_latest_release_and_forgets_withdrawn_datasets(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    assert _search(world).hits[0].release.label == 1
    world.curate(
        "orchard", {"op": "set", "descriptor": "dataset", "pointer": "/label", "value": "O"}
    )
    hit = _search(world).hits[0]
    assert (hit.release.label, hit.label.data) == (2, "O")
    world.store.withdraw("orchard", 2, "operator:Ada")
    assert _search(world).hits[0].release.label == 1
    world.store.withdraw("orchard", 1, "operator:Ada")
    assert _search(world).hits == []
    assert world.store.db.connection.execute("SELECT count(*) FROM catalog").fetchone()[0] == 0


def test_the_index_is_kept_in_the_app_db_and_built_again_for_another_floor(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    first = entries(world.store, None, None)
    rows = world.store.db.connection.execute("SELECT dataset, basis FROM catalog").fetchall()
    assert [row[0] for row in rows] == ["orchard"]
    assert entries(world.store, None, None) == first
    floored = entries(world.store, None, 30)
    assert floored[0].k == 30
    again = world.store.db.connection.execute("SELECT basis FROM catalog").fetchone()[0]
    assert again != rows[0][1]


# --- describe_dataset -------------------------------------------------------------------------


def _describe(world: World, catalog: Any = None, **given: JsonValue) -> DatasetDescription:
    catalog = catalog or world.catalog()
    return catalog.describe_dataset(DescribeDataset.model_validate(given))


def test_a_dataset_is_described_with_its_tables_columns_graph_and_its_analyses(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    found = _describe(world, dataset="orchard")
    assert found.release.label == 1
    assert found.release.status == "published"
    assert found.descriptor["kind"] == "dataset"
    assert [t.descriptor["id"] for t in found.tables] == ["harvests", "trees"]
    trees = found.tables[1]
    assert trees.rows.count == 24
    brief = {c.id: (c.datatype, c.identifier) for c in trees.columns}
    assert brief["trees.tree_id"] == ("string", True)
    assert brief["trees.variety"] == ("category", False)
    assert [r["id"] for r in found.relationships] == ["rel:harvests.tree_id"]
    assert found.graph.tables == ["harvests", "trees"]
    assert [(e.child, e.parent) for e in found.graph.edges] == [("harvests", "trees")]
    assert [(a.analysis, a.version, a.status, a.missing) for a in found.applicable_analyses] == [
        ("compare.existence", "1.0.0", "available", []),
        ("summary.distribution", "1.0.0", "available_with_caveats", []),
        ("summary.members", "1.0.0", "available", []),
    ]
    assert found.columns_total == 9
    assert found.columns_next is None


def test_standing_caveats_name_unconfirmed_fields_and_proposed_coverage(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard(sites=True))
    found = _describe(world, dataset="orchard")
    codes = _codes(found)
    assert "UNCONFIRMED_SEMANTICS" in codes
    proposed = [c for c in found.caveats if c.code == "COVERAGE_PROPOSED"]
    assert proposed
    for caveat in proposed:
        assert caveat.severity == "warn"
        assert caveat.affects[0].startswith("/coverage/")
    unconfirmed = next(c for c in found.caveats if c.code == "UNCONFIRMED_SEMANTICS")
    assert any(s == "harvests/fields/primary_key" for s in _data(unconfirmed.message))
    assert _data(proposed[0].message) == ["rel:trees.site_id"]


def test_confirming_fields_takes_them_out_of_the_standing_caveats(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    before = _describe(world, dataset="orchard")
    ids = [t.descriptor["id"] for t in before.tables]
    ids += [c.id for t in before.tables for c in t.columns]
    ids += [r["id"] for r in before.relationships]
    world.curate("orchard", *({"op": "confirm", "descriptor": id} for id in ids))
    after = _describe(world, dataset="orchard")
    message = next(c for c in after.caveats if c.code == "UNCONFIRMED_SEMANTICS").message
    named = _data(message)
    assert all(pointer.endswith(("/fields/units", "/fields/parents")) for pointer in named)
    assert "cov:harvests.tree_id/fields/parents" in named


def test_the_draft_is_described_when_pinned_and_carries_draft_release(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    opened = world.open("orchard")
    world.change(
        "orchard",
        opened,
        opened.draft,
        {"op": "set", "descriptor": "trees", "pointer": "/label", "value": "T"},
    )
    found = _describe(world, dataset="orchard", release="draft")
    assert (found.release.label, found.release.status) == ("draft", "draft")
    assert found.tables[1].descriptor["label"] == "T"
    draft = [c for c in found.caveats if c.code == "DRAFT_RELEASE"]
    assert [c.affects for c in draft] == [["/release"]]
    assert _describe(world, dataset="orchard").tables[1].descriptor["label"] == "trees"


def test_a_withdrawn_release_an_unknown_label_and_an_unknown_dataset_are_refused(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    world.curate("orchard", {"op": "set", "descriptor": "trees", "pointer": "/label", "value": "T"})
    world.store.withdraw("orchard", 1, "operator:Ada")
    with pytest.raises(ToolRefused) as withdrawn:
        _describe(world, dataset="orchard", release=1)
    refusal = withdrawn.value.refusals[0]
    assert (refusal.code, refusal.path) == ("RELEASE_WITHDRAWN", "/release")
    assert _data(refusal.alternatives) == ["2"]
    with pytest.raises(ToolRefused) as unknown:
        _describe(world, dataset="orchard", release=9)
    refusal = unknown.value.refusals[0]
    assert (refusal.code, refusal.path) == ("UNKNOWN_RELEASE", "/release")
    assert _data(refusal.alternatives) == ["2"]
    with pytest.raises(ToolRefused) as queued:
        _queue(world, dataset="orchard", release=1)
    assert queued.value.refusals[0].path == "/release"
    with pytest.raises(ToolRefused) as column:
        _column(world, dataset="orchard", column="trees.variety", release="draft")
    refusal = column.value.refusals[0]
    assert (refusal.code, refusal.path) == ("UNKNOWN_RELEASE", "/release")
    with pytest.raises(ToolRefused) as missing:
        _describe(world, dataset="orchards")
    refusal = missing.value.refusals[0]
    assert (refusal.code, refusal.path) == ("UNKNOWN_DATASET", "/dataset")
    assert _data(refusal.alternatives) == ["orchard"]


def test_a_release_swept_between_resolving_and_pinning_it_is_refused_as_withdrawn(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())

    def swept(self: Pin, manifest: str) -> Any:
        raise MissingBlobError(manifest.removeprefix("sha256:"))

    monkeypatch.setattr(Pin, "manifest", swept)
    with pytest.raises(ToolRefused) as described:
        _describe(world, dataset="orchard")
    refusal = described.value.refusals[0]
    assert (refusal.code, refusal.path) == ("RELEASE_WITHDRAWN", "/release")
    body: JsonValue = {"dataset": "orchard", "column": "trees.variety"}
    refusal = _refusal(world, world.catalog(), "describe_column", body)
    assert refusal.code == "RELEASE_WITHDRAWN"


def test_a_dataset_s_columns_are_listed_a_page_at_a_time(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    first = _describe(world, dataset="orchard", columns_limit=3)
    listed = [c.id for t in first.tables for c in t.columns]
    assert listed == ["harvests.grade", "harvests.harvest_id", "harvests.kg"]
    assert (first.columns_total, first.columns_next) == (9, 3)
    rest = _describe(world, dataset="orchard", columns_offset=3, columns_limit=100)
    assert len([c for t in rest.tables for c in t.columns]) == 6
    assert rest.columns_next is None


# --- describe_column --------------------------------------------------------------------------


def _column(world: World, catalog: Any = None, **given: JsonValue) -> ColumnDescription:
    catalog = catalog or world.catalog()
    return catalog.describe_column(DescribeColumn.model_validate(given))


def test_a_column_is_described_with_its_states_and_distribution(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard())
    found = _column(world, dataset="orchard", column="trees.variety")
    assert found.descriptor["id"] == "trees.variety"
    assert not found.identifier
    statistics = found.statistics
    assert statistics.rows.count == 24
    assert statistics.states.PRESENT.count == 24
    assert statistics.states.PRESENT.reference == (
        f"stat:{published.manifest}/trees.variety/states/PRESENT"
    )
    distribution = _dumped(found)["statistics"]["distribution"]
    assert [(c["value"]["data"], c["count"]) for c in distribution["categories"]] == [
        ("apple", 8),
        ("pear", 8),
        ("plum", 8),
    ]
    assert distribution["categories"][0]["reference"].endswith("/trees.variety/categories/apple")


def test_a_numeric_column_shows_its_histogram_and_extremes_without_a_setting(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    found = _dumped(_column(world, dataset="orchard", column="trees.height_m"))
    distribution = found["statistics"]["distribution"]
    assert distribution["kind"] == "histogram"
    assert distribution["edges_from"] == "data"
    assert sum(b["count"] for b in distribution["bins"]) == 21
    assert distribution["min"]["value"] == 1.25
    assert distribution["max"]["reference"].endswith("/trees.height_m/max")


def test_an_identifier_column_has_no_distribution(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    found = _column(world, dataset="orchard", column="harvests.tree_id")
    assert found.identifier
    assert _dumped(found)["statistics"]["distribution"] == {"kind": "none", "reason": "identifier"}


def test_a_column_under_a_floor_is_disclosed_and_caveated(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    catalog = world.catalog(floor=5)
    found = _column(world, catalog, dataset="orchard", column="trees.height_m")
    assert found.disclosure.min_cell_count == 5
    assert found.statistics.states.UNKNOWN.count is None
    assert found.statistics.states.PRESENT.count is None
    assert _dumped(found)["statistics"]["distribution"] == {"kind": "none", "reason": "suppressed"}
    assert "SUPPRESSED" in _codes(found)
    assert found.statistics.rows.reference.endswith("?floor=5")


def test_a_dataset_s_own_setting_discloses_its_columns(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    world.curate(
        "orchard",
        {
            "op": "set",
            "descriptor": "dataset",
            "pointer": "/fields/disclosure",
            "value": {"min_cell_count": 9, "allow_row_ids": True},
        },
    )
    found = _column(world, dataset="orchard", column="trees.variety")
    assert found.disclosure.min_cell_count == 9
    assert found.statistics.rows.reference.endswith("/trees/n_rows")
    pooled = _dumped(found)["statistics"]["distribution"]
    assert pooled["categories"] == []
    assert pooled["pooled"]["count"] == 24
    assert "SUPPRESSED" in _codes(found)


def test_an_unknown_column_is_refused_with_the_table_s_columns(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    with pytest.raises(ToolRefused) as refused:
        _column(world, dataset="orchard", column="trees.colour")
    refusal = refused.value.refusals[0]
    assert (refusal.code, refusal.path) == ("UNKNOWN_COLUMN", "/column")
    assert "trees.variety" in _data(refusal.alternatives)
    with pytest.raises(ToolRefused) as table:
        _column(world, dataset="orchard", column="shrubs.colour")
    assert table.value.refusals[0].code == "UNKNOWN_TABLE"


# --- curation_queue ---------------------------------------------------------------------------


def _queue(world: World, catalog: Any = None, **given: JsonValue) -> QueueOut:
    catalog = catalog or world.catalog()
    return catalog.curation_queue(QueueRequest.model_validate(given))


def test_the_queue_s_counts_carry_references_to_the_import_report(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard(unparsed=1))
    found = _queue(world, dataset="orchard")
    counted = [(i, n) for i, n in enumerate(found.queue.notes) if n.count is not None]
    assert counted
    for index, note in counted:
        assert note.reference == f"stat:{published.manifest}/dataset/report/{index}"
    unparsed = next(n for n in found.queue.notes if n.kind == "unparsed")
    assert (unparsed.count, unparsed.rows) == (1, [1])
    assert found.caveats == []


def test_a_small_count_in_the_queue_is_suppressed_with_its_rows(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard(unparsed=1))
    found = _queue(world, world.catalog(floor=5), dataset="orchard")
    unparsed = next(n for n in found.queue.notes if n.kind == "unparsed")
    dumped = _dumped(unparsed)
    assert dumped["count"] is None
    assert dumped["rows"] == []
    assert dumped["not_estimable"] == {"/count": "suppressed"}
    assert dumped["reference"].endswith("?floor=5")
    assert _codes(found) == ["SUPPRESSED"]


# --- propose_descriptor -----------------------------------------------------------------------


def _propose(world: World, catalog: Any = None, **given: JsonValue) -> Any:
    catalog = catalog or world.catalog()
    return catalog.propose_descriptor(ProposeDescriptor.model_validate(given))


def test_an_agent_s_proposal_is_attributed_to_it_and_waits_in_the_queue(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard())
    proposed = _propose(
        world,
        dataset="orchard",
        agent="survey bot",
        descriptor="trees.height_m",
        pointer="/fields/units",
        value="m",
        evidence="The header says metres",
    )
    assert (proposed.by, proposed.release, proposed.label) == (
        "agent:survey bot",
        published.manifest,
        1,
    )
    queued = _queue(world, dataset="orchard").queue.proposals
    assert [(p.id, p.proposer, p.value) for p in queued] == [
        (proposed.proposal, "agent:survey bot", "m")
    ]
    assert world.store.latest("orchard").manifest == published.manifest


def test_a_proposal_that_does_not_hold_is_refused_at_its_pointer(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    refusal = _refusal(
        world,
        world.catalog(),
        "propose_descriptor",
        {
            "dataset": "orchard",
            "agent": "bot",
            "descriptor": "trees.height_m",
            "pointer": "/fields/datatype",
            "value": "colour",
        },
    )
    assert refusal.path is not None
    assert refusal.path.startswith("/descriptors/trees.height_m")


@pytest.mark.parametrize(
    ("member", "given"),
    [
        ("evidence", "the token aibi_" + "A" * 43),
        ("value", "ses_" + "b" * 43),
    ],
)
def test_a_proposal_that_would_store_a_secret_is_refused(
    world: World, orchard: Orchard, member: str, given: str
) -> None:
    world.publish("orchard", orchard())
    body: dict[str, JsonValue] = {
        "dataset": "orchard",
        "agent": "bot",
        "descriptor": "trees",
        "pointer": "/definition",
        "value": "Trees",
        member: given,
    }
    refusal = _refusal(world, world.catalog(), "propose_descriptor", body)
    assert (refusal.code, refusal.path) == ("INVALID_VALUE", f"/{member}")
    assert given not in refusal.model_dump_json()


@pytest.mark.parametrize("agent", ["", "a\nb", "right‮left", "x" * 201, "aibi_" + "c" * 43])
def test_an_agent_s_name_is_a_name_of_section_5_1(
    world: World, orchard: Orchard, agent: str
) -> None:
    world.publish("orchard", orchard())
    body = {"dataset": "orchard", "agent": agent, "descriptor": "trees", "pointer": "/label"}
    refusal = _refusal(world, world.catalog(), "propose_descriptor", {**body, "value": "T"})
    assert refusal.path == "/agent"


def test_a_long_word_that_holds_a_secret_s_shape_is_a_name_and_a_value(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    word = "courses_aibi_" + "c" * 43 + "s"
    body: JsonValue = {
        "dataset": "orchard",
        "agent": word,
        "descriptor": "trees",
        "pointer": "/definition",
        "value": word,
        "evidence": f"see {word}",
    }
    proposed = world.tool(world.catalog(), "propose_descriptor", body)
    assert isinstance(proposed, Proposed), proposed
    assert proposed.by == f"agent:{word}"


# --- Resources --------------------------------------------------------------------------------

CARD = ModelCardDescriptor.model_validate(
    {
        "kind": "model",
        "id": "model:helper",
        "version": "1.0.0",
        "label": "Helper",
        "fields": {
            "provider": "a provider",
            "model": "a model",
            "model_version": "1",
            "purpose": ["drafting"],
            "limitations": "Drafts only",
            "configuration_digest": "sha256:" + "0" * 64,
        },
    }
)


def test_resources_list_the_latest_dataset_descriptors_concepts_and_model_cards(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    listed = dict(world.catalog(models=[CARD]).resources())
    assert "aibi://dataset/orchard@1/dataset" in listed
    assert "aibi://concept/core:person" in listed
    assert "aibi://model/model:helper" in listed


def test_every_descriptor_reads_as_a_resource_by_label_hash_or_draft(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard())
    catalog = world.catalog(models=[CARD])
    by_label = json.loads(catalog.read_resource("aibi://dataset/orchard@1/trees.variety"))
    by_hash = catalog.read_resource(f"aibi://dataset/orchard@{published.manifest}/trees.variety")
    assert by_label == json.loads(by_hash)
    assert by_label["id"] == "trees.variety"
    world.open("orchard")
    assert json.loads(catalog.read_resource("aibi://dataset/orchard@draft/trees"))["id"] == "trees"
    assert json.loads(catalog.read_resource("aibi://concept/core:sex"))["kind"] == "concept"
    assert json.loads(catalog.read_resource("aibi://model/model:helper"))["id"] == "model:helper"


@pytest.mark.parametrize(
    "uri",
    [
        "aibi://dataset/orchard@1/shrubs",
        "aibi://dataset/orchard@1/a__b",
        "aibi://concept/core:colour",
        "aibi://model/model:nobody",
        "aibi://analysis/summary.distribution@9.0.0",
        "https://example.org/dataset",
        "aibi://dataset/orchard/trees",
        "aibi://dataset/orchard@1/" + "t" * 3000,
    ],
)
def test_a_resource_that_is_not_there_is_refused(world: World, orchard: Orchard, uri: str) -> None:
    world.publish("orchard", orchard())
    with pytest.raises(ToolRefused) as refused:
        world.catalog().read_resource(uri)
    assert refused.value.refusals[0].code == "NOT_FOUND"


def _counted(world: World, manifest: str) -> dict[str, Any]:
    descriptors = world.store.descriptors(manifest)
    marked = statistics.identifiers(descriptors)
    columns: dict[str, dict[str, Any]] = {}
    for descriptor in descriptors:
        if descriptor.kind == "column":
            table, column = descriptor.id.split(".", 1)
            columns.setdefault(table, {})[column] = descriptor.fields
    found: dict[str, Any] = {}
    for entry in world.store.manifest(manifest).tables:
        fields = columns.get(entry.id, {})
        typed = tables.decode_cells(entry.id, world.store.blobs.read(entry.hash), list(fields))
        found[entry.id] = statistics.table_statistics(
            typed.rows, typed.cells, fields, marked.get(entry.id, set())
        )
    return found


def test_statistics_counted_again_from_the_blobs_are_those_the_build_wrote(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard(sites=True))
    manifest = world.store.manifest(published.manifest)
    assert manifest.statistics is not None
    written = statistics.decode(world.store.blobs.read(manifest.statistics))
    assert _counted(world, published.manifest) == written


def test_a_release_without_statistics_is_refused_and_left_out_of_the_search_reading_no_rows(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = world.publish("orchard", orchard(sites=True))
    world.publish("bakery", BAKERY)
    assert [hit.dataset for hit in _search(world).hits] == ["bakery", "orchard"]
    pinned = Pin.manifest

    def uncounted(self: Pin, manifest: str) -> Any:
        found = pinned(self, manifest)
        if manifest != published.manifest:
            return found
        return found.model_copy(update={"statistics": None})

    def no_rows(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the catalogue read a table")

    monkeypatch.setattr(Pin, "manifest", uncounted)
    monkeypatch.setattr(tables, "decode_cells", no_rows)
    catalog = world.catalog(floor=5)
    assert [hit.dataset for hit in _search(world, catalog={"floor": 5}).hits] == ["bakery"]
    rows = world.store.db.connection.execute("SELECT dataset FROM catalog").fetchall()
    assert [row[0] for row in rows] == ["bakery"]
    asked: list[tuple[str, JsonValue]] = [
        ("describe_dataset", {"dataset": "orchard"}),
        ("describe_column", {"dataset": "orchard", "column": "trees.height_m"}),
        ("curation_queue", {"dataset": "orchard"}),
    ]
    for name, body in asked:
        refusal = _refusal(world, catalog, name, body)
        assert (refusal.code, refusal.path) == ("NOT_SUPPORTED", "/release")
        assert [segment.model_dump() for segment in refusal.alternatives] == [{"data": "1"}]
    queue = world.tool(world.catalog(), "curation_queue", {"dataset": "orchard"})
    assert isinstance(queue, QueueOut), queue


def test_an_erasure_s_redaction_deletes_the_dataset_s_catalogue_entry(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    world.publish("bakery", BAKERY)
    entries(world.store, None, None)
    redactor: Any = REDACTORS["catalog"]
    with world.store.db.transaction() as db:
        assert redactor(db, "orchard", None) == 1
    rows = world.store.db.connection.execute("SELECT dataset FROM catalog").fetchall()
    assert [row[0] for row in rows] == ["bakery"]
    assert [h.dataset for h in _search(world).hits] == ["bakery", "orchard"]


# --- Disclosure of everything a tool serves -----------------------------------------------------


def _readings(unparsed: int = 5, assessed: int = 2) -> dict[str, bytes]:
    """200 readings: ``unparsed`` of them do not parse, ``assessed`` are ``NA``."""
    lines = ["reading_id,value"]
    for n in range(1, 201):
        value = "lots" if n <= unparsed else "NA" if n <= unparsed + assessed else str(n % 40)
        lines.append(f"r{n},{value}")
    return {"readings.csv": ("\n".join(lines) + "\n").encode()}


def test_an_unparsed_count_is_suppressed_with_its_column_s_unknown_count(world: World) -> None:
    world.publish("meter", _readings())
    world.curate(
        "meter",
        {
            "op": "set",
            "descriptor": "readings.value",
            "pointer": "/fields/missing_codes",
            "value": {"NA": "NOT_ASSESSED"},
        },
    )
    catalog = world.catalog(floor=5)
    states = _column(world, catalog, dataset="meter", column="readings.value").statistics.states
    assert (states.PRESENT.count, states.NOT_APPLICABLE.count) == (193, 0)
    assert (states.NOT_ASSESSED.count, states.UNKNOWN.count) == (None, None)
    found = _queue(world, catalog, dataset="meter")
    unparsed = next(n for n in found.queue.notes if n.kind == "unparsed")
    assert (unparsed.count, unparsed.rows) == (None, [])
    assert "SUPPRESSED" in _codes(found)


def test_an_unparsed_count_other_than_its_column_s_unknown_count_is_suppressed(
    world: World,
) -> None:
    world.publish("meter", _readings())
    catalog = world.catalog(floor=5)
    column = _column(world, catalog, dataset="meter", column="readings.value")
    assert column.statistics.states.UNKNOWN.count == 7
    unparsed = next(
        n for n in _queue(world, catalog, dataset="meter").queue.notes if n.kind == "unparsed"
    )
    assert unparsed.count is None


@pytest.mark.parametrize(("floor", "shown"), [(2, 2), (3, None)])
def test_an_unparsed_count_is_shown_while_it_is_its_column_s_unknown_count_as_disclosed(
    world: World, orchard: Orchard, floor: int, shown: int | None
) -> None:
    world.publish("orchard", orchard(24, harvests=40, unparsed=2))
    catalog = world.catalog(floor=floor)
    column = _column(world, catalog, dataset="orchard", column="harvests.kg")
    assert column.statistics.states.UNKNOWN.count == shown
    found = _queue(world, catalog, dataset="orchard")
    unparsed = next(n for n in found.queue.notes if n.kind == "unparsed")
    assert unparsed.count == shown
    assert unparsed.reference is not None


def test_under_a_setting_the_queue_carries_no_evidence_and_notes_in_fixed_words(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard(24, harvests=40, unparsed=2))
    _propose(
        world,
        dataset="orchard",
        agent="bot",
        descriptor="trees.height_m",
        pointer="/fields/units",
        value="m",
        evidence="The header says metres",
    )
    open_queue = _queue(world, dataset="orchard").queue
    assert any(f.evidence for f in open_queue.fields)
    assert open_queue.proposals[0].evidence == "The header says metres"
    found = _queue(world, world.catalog(floor=2), dataset="orchard").queue
    assert [f.evidence for f in found.fields] == [None] * len(found.fields)
    assert found.proposals[0].evidence is None
    assert {note.kind for note in found.notes} >= {"unparsed"}
    for note in found.notes:
        assert [segment.model_dump() for segment in note.message] == [
            {"text": NOTE_TEXT[note.kind]}
        ]


def test_under_a_setting_descriptors_are_served_without_their_evidence(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    column = _column(world, dataset="orchard", column="trees.height_m")
    assert "evidence" in cast(dict[str, Any], column.descriptor["curation"])["/fields/datatype"]
    catalog = world.catalog(floor=2)
    floored = _column(world, catalog, dataset="orchard", column="trees.height_m")
    described = _describe(world, catalog, dataset="orchard")
    resource = json.loads(catalog.read_resource("aibi://dataset/orchard@1/trees"))
    served = [
        floored.descriptor,
        described.descriptor,
        *(t.descriptor for t in described.tables),
        *described.relationships,
        *described.coverage,
        resource,
    ]
    for descriptor in served:
        entries = cast(dict[str, dict[str, Any]], descriptor["curation"])
        assert entries
        assert all("evidence" not in entry for entry in entries.values()), descriptor["id"]


def test_a_draft_is_disclosed_under_the_published_release_s_setting_too(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    world.curate(
        "orchard",
        {
            "op": "set",
            "descriptor": "dataset",
            "pointer": "/fields/disclosure",
            "value": {"min_cell_count": 5, "allow_row_ids": False},
        },
    )
    opened = world.open("orchard")
    world.change(
        "orchard",
        opened,
        opened.draft,
        {"op": "remove", "descriptor": "dataset", "pointer": "/fields/disclosure"},
    )
    found = _column(world, dataset="orchard", column="trees.height_m", release="draft")
    assert found.disclosure.min_cell_count == 5
    assert found.statistics.states.UNKNOWN.count is None
    assert _dumped(found)["statistics"]["distribution"]["kind"] == "none"
    draft = json.loads(world.catalog().read_resource("aibi://dataset/orchard@draft/trees"))
    assert all("evidence" not in entry for entry in draft["curation"].values())


def test_the_suppressed_caveat_of_a_description_points_at_rows_only_when_they_are_null(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard(24, harvests=40))
    shown = _describe(world, world.catalog(floor=5), dataset="orchard")
    assert "SUPPRESSED" not in _codes(shown)
    hidden = _describe(world, world.catalog(floor=30), dataset="orchard")
    suppressed = next(c for c in hidden.caveats if c.code == "SUPPRESSED")
    assert suppressed.affects == ["/tables/1/rows"]


# --- The index, search boundaries and pages ----------------------------------------------------


def test_an_entry_built_across_a_withdrawal_of_its_release_is_not_kept(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    world.curate("orchard", {"op": "set", "descriptor": "trees", "pointer": "/label", "value": "T"})
    built = indexing.build_entry
    withdrawn: list[int] = []

    def racing(*args: Any) -> Any:
        found = built(*args)
        if not withdrawn:
            withdrawn.extend(world.store.withdraw("orchard", 2, "operator:Ada"))
        return found

    monkeypatch.setattr(indexing, "build_entry", racing)
    assert entries(world.store, None, None) == []
    assert withdrawn == [2]
    assert world.store.db.connection.execute("SELECT count(*) FROM catalog").fetchone()[0] == 0
    assert [entry.label for entry in entries(world.store, None, None)] == [1]


def _sparse(rows: int, present: int) -> dict[str, bytes]:
    lines = ["item_id,level"] + [
        f"i{n},{n % 9 if n <= present else ''}" for n in range(1, rows + 1)
    ]
    return {"items.csv": ("\n".join(lines) + "\n").encode()}


@pytest.mark.parametrize(("present", "found"), [(7, True), (6, False)])
def test_completeness_meets_its_threshold_exactly(world: World, present: int, found: bool) -> None:
    world.publish("stock", _sparse(100, present))
    hits = _search(world, completeness={"min_present": 0.07, "datatype": "integer"}).hits
    assert bool(hits) is found


def test_a_coverage_table_s_rows_meet_no_least_number_of_rows(world: World) -> None:
    files = {
        "shops.csv": b"shop_id,town\ns1,a\ns2,b\ns3,c\n",
        "listing.csv": b"entry_id,note\n"
        + b"".join(f"e{n},n{n % 3}\n".encode() for n in range(1, 41)),
    }
    world.publish("shops", files)
    assert [h.dataset for h in _search(world, min_rows=30).hits] == ["shops"]
    world.curate(
        "shops",
        {"op": "set", "descriptor": "listing", "pointer": "/fields/role", "value": "coverage"},
    )
    assert _search(world, min_rows=30).hits == []
    assert [h.dataset for h in _search(world, min_rows=3).hits] == ["shops"]


def test_a_page_that_ends_at_the_last_hit_has_no_next_offset(world: World) -> None:
    for dataset in ("a", "b", "c"):
        world.publish(dataset, BAKERY)
    found = _search(world, limit=2, offset=1)
    assert [h.dataset for h in found.hits] == ["b", "c"]
    assert found.next_offset is None


def test_a_page_of_columns_that_ends_at_the_last_column_has_no_next_offset(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    assert _describe(world, dataset="orchard", columns_limit=9).columns_next is None
    assert (
        _describe(world, dataset="orchard", columns_offset=3, columns_limit=6).columns_next is None
    )
    assert _describe(world, dataset="orchard", columns_limit=8).columns_next == 8


def test_the_standing_caveat_names_units_of_numeric_columns_alone(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    found = _describe(world, dataset="orchard")
    named = _data(next(c for c in found.caveats if c.code == "UNCONFIRMED_SEMANTICS").message)
    assert "trees.height_m/fields/units" in named
    assert "trees.variety/fields/units" not in named
    assert "harvests.grade/fields/units" not in named


# --- Packs -------------------------------------------------------------------------------------


class Rewriting:
    """The core's file importer, whose descriptors ``rewrite`` changes as a pack's would."""

    def __init__(self, rewrite: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.rewrite = rewrite

    def import_source(self, source: Any, options: Any) -> Any:
        result = FileImporter().import_source(source, options)
        rewritten = [
            TypeAdapter(Descriptor).validate_python(self.rewrite(d.model_dump(mode="json")))
            for d in result.descriptors
        ]
        return replace(result, descriptors=rewritten)


def _pack(
    rewrite: Callable[[dict[str, Any]], dict[str, Any]] = lambda d: d, version: str = "1.0.0"
) -> PackRegistry:
    pack = Pack(
        manifest=PackManifest(
            id="rewriting", version=version, results_version=1, requires_core=">=0.0.1"
        ),
        importer=Rewriting(rewrite),
    )
    return PackRegistry([pack], core_version=aibi.__version__)


def _proposed_person(descriptor: dict[str, Any]) -> dict[str, Any]:
    if descriptor["id"] == "staff":
        descriptor["fields"]["maps_to"] = {"concept": "core:person"}
        entry = dict(descriptor["curation"]["/label"])
        descriptor["curation"]["/fields/maps_to"] = {**entry, "status": "proposed"}
    return descriptor


def test_a_proposed_mapping_is_no_concept_of_the_catalogue(world: World) -> None:
    registry: Any = _pack(_proposed_person)
    world.publish("bakery", BAKERY, registry=registry, pack="rewriting")
    assert _search(world, catalog={"registry": registry}, concepts=["core:person"]).hits == []
    assert _search(world, catalog={"registry": registry}).hits[0].concepts == []


def _defaulted_coverage(descriptor: dict[str, Any]) -> dict[str, Any]:
    if descriptor["kind"] == "coverage":
        entry = descriptor["curation"]["/fields/parents"]
        descriptor["curation"]["/fields/parents"] = {**entry, "status": "imported_default"}
    return descriptor


def test_a_coverage_imported_by_default_is_a_standing_unconfirmed_field(
    world: World, orchard: Orchard
) -> None:
    registry = _pack(_defaulted_coverage)
    world.publish("orchard", orchard(sites=True), registry=registry, pack="rewriting")
    found = _describe(world, world.catalog(registry=registry), dataset="orchard")
    unconfirmed = next(c for c in found.caveats if c.code == "UNCONFIRMED_SEMANTICS")
    assert "cov:trees.site_id/fields/parents" in _data(unconfirmed.message)
    assert not [c for c in found.caveats if c.code == "COVERAGE_PROPOSED"]


def test_a_pack_s_version_is_part_of_an_entry_s_basis() -> None:
    manifest = "sha256:" + "ab" * 32
    assert indexing.basis(manifest, None, _pack()) == indexing.basis(manifest, None, _pack())
    assert indexing.basis(manifest, None, _pack()) != indexing.basis(
        manifest, None, _pack(version="1.1.0")
    )
    assert indexing.basis(manifest, None, _pack()) != indexing.basis(manifest, 5, _pack())


def test_a_dataset_that_lists_a_pack_the_server_does_not_run_has_no_facets(
    world: World, orchard: Orchard, orchards: Callable[..., Any]
) -> None:
    world.publish("orchard", orchard(), registry=orchards(), pack="orchards")
    none: Any = PackRegistry((), core_version=aibi.__version__)
    hit = _search(world, catalog={"registry": none}).hits[0]
    assert (hit.packs, hit.facets) == (["orchards"], {})


# --- Agents' proposals -------------------------------------------------------------------------


def test_agents_hold_a_share_of_a_dataset_s_open_proposals(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    monkeypatch.setattr(proposals, "MAX_AGENT_PROPOSALS", 1)
    body: dict[str, JsonValue] = {
        "dataset": "orchard",
        "descriptor": "trees.height_m",
        "pointer": "/fields/units",
    }
    first = world.tool(world.catalog(), "propose_descriptor", {**body, "agent": "a", "value": "m"})
    assert isinstance(first, Proposed), first
    refusal = _refusal(
        world, world.catalog(), "propose_descriptor", {**body, "agent": "b", "value": "cm"}
    )
    assert refusal.code == "LIMIT_EXCEEDED"
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == ("agent_proposals", 1)
    del body["dataset"]
    request = ProposalInput.model_validate({**body, "value": "mm"})
    assert proposals.propose_descriptor(world.store, "orchard", request, "model:helper") > 0


def test_the_agents_of_one_client_hold_a_share_of_the_agents_proposals(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    monkeypatch.setattr(proposals, "MAX_CLIENT_PROPOSALS", 1)
    tool = BY_NAME["propose_descriptor"]
    body: dict[str, JsonValue] = {
        "dataset": "orchard",
        "descriptor": "trees.height_m",
        "pointer": "/fields/units",
    }

    def proposed(client: str, agent: str, value: str) -> Output | list[Refusal]:
        given = {**body, "agent": agent, "value": value}
        return call(world.catalog(), tool, json.dumps(given).encode(), client=client)

    assert isinstance(proposed("192.0.2.1", "a", "m"), Proposed)
    refused = proposed("192.0.2.1", "b", "cm")
    assert isinstance(refused, list)
    assert refused[0].limit is not None
    assert (refused[0].code, refused[0].limit.name, refused[0].limit.max) == (
        "LIMIT_EXCEEDED",
        "client_proposals",
        1,
    )
    assert isinstance(proposed("198.51.100.7", "b", "cm"), Proposed)
    del body["dataset"]
    request = ProposalInput.model_validate({**body, "value": "mm"})
    assert proposals.propose_descriptor(world.store, "orchard", request, "model:helper") > 0


def _agent_proposal(world: World, agent: str, value: str) -> int:
    request = ProposalInput.model_validate(
        {"descriptor": "trees.height_m", "pointer": "/fields/units", "value": value}
    )
    return proposals.propose_descriptor(
        world.store, "orchard", request, f"agent:{agent}", client="192.0.2.1"
    )


def test_an_operator_rejects_every_open_proposal_of_a_proposer_or_of_every_agent_at_once(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    _agent_proposal(world, "a", "m")
    _agent_proposal(world, "b", "cm")
    _agent_proposal(world, "a", "mm")
    request = ProposalInput.model_validate(
        {"descriptor": "trees.height_m", "pointer": "/fields/units", "value": "km"}
    )
    proposals.propose_descriptor(world.store, "orchard", request, "model:helper")
    ada = "operator:Ada"
    assert proposals.reject_proposals(world.store, "orchard", ada, proposer="agent:a") == (2, 0)
    open_ = [p.proposer for p in world.store.db.proposals("orchard")]
    assert open_ == ["agent:b", "model:helper"]
    assert proposals.reject_proposals(world.store, "orchard", ada, kind="agent") == (1, 0)
    assert [p.proposer for p in world.store.db.proposals("orchard")] == ["model:helper"]
    audited = world.store.db.connection.execute(
        "SELECT actor, detail FROM audit WHERE action = 'reject_proposals' ORDER BY id"
    ).fetchall()
    assert [(row[0], json.loads(row[1])) for row in audited] == [
        (ada, {"kept": 0, "proposer": "agent:a", "rejected": 2}),
        (ada, {"kept": 0, "proposer": "agent:*", "rejected": 1}),
    ]
    for given in (
        {},
        {"proposer": "agent:a", "kind": "agent"},
        {"proposer": "operator:Ada"},
        {"kind": "operator"},
    ):
        with pytest.raises(StoreRefused) as refused:
            proposals.reject_proposals(world.store, "orchard", ada, **given)
        assert refused.value.refusal.code == "INVALID_VALUE"
    with pytest.raises(StoreRefused):
        proposals.reject_proposals(world.store, "orchard", "agent:a", kind="agent")


def test_a_proposal_the_open_draft_holds_is_kept_when_its_proposer_s_are_rejected(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    held = _agent_proposal(world, "a", "m")
    _agent_proposal(world, "a", "cm")
    opened = world.open("orchard")
    world.change("orchard", opened, opened.draft, {"op": "accept", "proposal": held})
    assert proposals.reject_proposals(world.store, "orchard", "operator:Ada", kind="agent") == (
        1,
        1,
    )
    assert [p.id for p in world.store.db.proposals("orchard")] == [held]


def test_a_draft_s_setting_skips_withdrawn_releases_whose_descriptors_are_gone(
    world: World, orchard: Orchard
) -> None:
    """The store's cache of descriptors is emptied, as a restarted server's is."""
    world.publish("orchard", orchard())
    world.curate(
        "orchard",
        {
            "op": "set",
            "descriptor": "dataset",
            "pointer": "/fields/disclosure",
            "value": {"min_cell_count": 5, "allow_row_ids": False},
        },
    )
    withdrawn = world.store.labels("orchard")[1].manifest
    world.store.withdraw("orchard", 2, "operator:Ada")
    assert world.store.blobs.exists(withdrawn.removeprefix("sha256:"))
    world.store._descriptors.clear()  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(MissingBlobError):
        world.store.descriptors(withdrawn)
    world.open("orchard")
    found = _describe(world, dataset="orchard", release="draft")
    assert found.disclosure.min_cell_count is None
    assert found.release.status == "draft"


def test_the_public_queue_leaves_hidden_text_out_before_it_spends_its_byte_budget(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard(24, harvests=40, unparsed=2))
    for value in ("m", "cm"):
        _propose(
            world,
            dataset="orchard",
            agent="bot",
            descriptor="trees.height_m",
            pointer="/fields/units",
            value=value,
            evidence="e" * 4_000,
        )
    whole = _queue(world, world.catalog(floor=2), dataset="orchard").queue
    kept = [*whole.fields, *whole.undeclared, *whole.proposals]
    budget = sum(len(item.model_dump_json().encode()) for item in kept)
    monkeypatch.setattr(proposals, "MAX_QUEUE_BYTES", budget)
    under = _queue(world, world.catalog(floor=2), dataset="orchard").queue
    assert len(under.proposals) == 2
    assert (under.notes, under.truncated) == ([], len(whole.notes))
    raw = _queue(world, dataset="orchard").queue
    assert raw.truncated > under.truncated


class Noting:
    """The core's file importer, with ``notes`` added to its report as a pack's would be."""

    def __init__(self, notes: list[ImportNote]) -> None:
        self.notes = notes

    def import_source(self, source: Any, options: Any) -> Any:
        result = FileImporter().import_source(source, options)
        return replace(result, notes=[*result.notes, *self.notes])


def test_under_a_setting_a_pack_s_notes_are_in_fixed_words_counted_as_disclosed_and_rowless(
    world: World, orchard: Orchard
) -> None:
    written = [
        ImportNote(
            kind="dropped",
            subject="harvests.kg",
            message=[text("Dropped: its evidence said 3 of 7 rows parse")],
            count=3,
            rows=(1, 2),
        ),
        ImportNote(
            kind="gap",
            subject="harvests.kg",
            message=[text("Rows the coding misses")],
            count=2,
            rows=(3, 4),
        ),
        ImportNote(kind="renamed", subject="trees", message=[text("Rows 5 and 6")], rows=(5, 6)),
    ]
    pack = Pack(
        manifest=PackManifest(
            id="noting", version="1.0.0", results_version=1, requires_core=">=0.0.1"
        ),
        importer=Noting(written),
    )
    registry = PackRegistry([pack], core_version=aibi.__version__)
    world.publish("orchard", orchard(24, harvests=40, unparsed=2), registry=registry, pack="noting")
    column = _column(world, world.catalog(floor=2), dataset="orchard", column="harvests.kg")
    assert column.statistics.states.UNKNOWN.count == 2
    raw = {note.kind: note for note in _queue(world, dataset="orchard").queue.notes}
    assert [s.model_dump() for s in raw["dropped"].message] == [
        {"text": "Dropped: its evidence said 3 of 7 rows parse"}
    ]
    assert (raw["dropped"].count, raw["dropped"].rows) == (3, [1, 2])
    assert raw["renamed"].rows == [5, 6]
    found = _queue(world, world.catalog(floor=2), dataset="orchard")
    shown = {note.kind: note for note in found.queue.notes}
    for kind in ("dropped", "gap", "renamed"):
        assert [s.model_dump() for s in shown[kind].message] == [{"text": NOTE_TEXT[kind]}]
        assert shown[kind].rows == []
    assert (shown["dropped"].count, shown["gap"].count, shown["renamed"].count) == (None,) * 3
    assert shown["gap"].not_estimable == {"/count": "suppressed"}
    assert shown["renamed"].reference is None
    assert "SUPPRESSED" in _codes(found)


def test_the_core_importer_writes_no_descriptor_text_computed_from_the_rows(
    world: World,
) -> None:
    few = world.publish("few", {"loaves.csv": b"loaf_id,flour,grams\nl1,rye,400\nl2,rye,410\n"})
    many = world.publish(
        "many",
        {
            "loaves.csv": b"loaf_id,flour,grams\n"
            + b"".join(f"l{n},{'wheat' if n % 3 else 'spelt'},{n}\n".encode() for n in range(90))
        },
    )

    def texts(manifest: str) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for descriptor in world.store.descriptors(manifest):
            dumped = descriptor.model_dump(mode="json")
            fields = dumped.get("fields") or {}
            found[descriptor.id] = (
                dumped["label"],
                dumped.get("definition"),
                fields.get("description"),
                fields.get("name"),
                dumped.get("extensions"),
            )
        return found

    few_texts, many_texts = texts(few.manifest), texts(many.manifest)
    del few_texts["dataset"], many_texts["dataset"]
    shared = sorted(set(few_texts) & set(many_texts))
    assert shared
    assert {i: few_texts[i] for i in shared} == {i: many_texts[i] for i in shared}

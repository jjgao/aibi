"""``run_analysis`` of a pack's analysis over a store (SPEC §8.4, §10.1, §12.2; D341–D344): the
orchard, imported by the core's file importer, and a test-only pack, ``groves``, whose analysis
echoes its inputs. The inputs a query worker lists are held to the reference evaluator's by the
result's digest."""

import json
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

import pytest
from pydantic import JsonValue

import aibi
from aibi.core.analyses import packs, views
from aibi.core.analyses.existence import CohortAt
from aibi.core.analyses.registry import Analyses
from aibi.core.analyses.results import envelope
from aibi.core.catalog import analyses as analyses_module
from aibi.core.catalog.cohorts import RECORD_SECONDS
from aibi.core.catalog.service import Catalog, Deadline, within
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.engine import sql as sql_module
from aibi.core.engine.canonical import canonicalise
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.inputs import listed, ordered, shared
from aibi.core.engine.worker import Workers
from aibi.core.schema.analyses import PackParams
from aibi.core.schema.catalog import AnalysisListing
from aibi.core.schema.cohorts import AnalysisResults, Explanation
from aibi.core.schema.descriptors import AnalysisDescriptor
from aibi.core.schema.limits import QueryLimits
from aibi.core.schema.loading import load_document
from aibi.core.schema.output import Output
from aibi.core.schema.pack_api import AnalysisInputs, Pack, PackManifest, PackRegistry
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode
from aibi.core.schema.results import ResultEnvelope

World = Any
Orchard = Callable[..., dict[str, bytes]]
WORKERS = Workers(QueryLimits())
APPLE = {"kind": "value", "column": "trees.variety", "values": ["apple"]}
PEAR = {"kind": "value", "column": "trees.variety", "values": ["pear"]}
COLUMNS = {
    "measure": [
        {"column": "trees.height_m"},
        {"column": "harvests.kg", "aggregate": "mean"},
    ],
    "extra": [{"column": "trees.tags", "aggregate": "some", "values": ["old"]}],
}


def entry(*, independent: bool = False) -> AnalysisDescriptor:
    return AnalysisDescriptor.model_validate(
        {
            "kind": "analysis",
            "id": "groves.echo",
            "version": "1.0.0",
            "label": "Echo",
            "fields": {
                "requires": [
                    {"role": "cohorts", "min": 1, "max": 6},
                    {"role": "measure", "kind": "column", "min": 1},
                    {"role": "extra", "kind": "column", "min": 0},
                ],
                "params": {"type": "object"},
                "returns": {"type": "object"},
                "methods": {},
                "assumptions": [],
                "uses_reference": False,
                "assumes_independent_groups": independent,
                "cross_dataset": None,
                "caveats": [],
            },
        }
    )


class Echo:
    def __init__(self, found: AnalysisDescriptor) -> None:
        self._entry = found
        self.handed: list[AnalysisInputs] = []

    @property
    def entry(self) -> AnalysisDescriptor:
        return self._entry

    def run(self, inputs: AnalysisInputs) -> Mapping[str, JsonValue]:
        self.handed.append(inputs)
        return {
            "positions": [
                {
                    "units": position.units,
                    "values": [list(column) for column in position.values],
                    "excluded": [[list(r) for r in column] for column in position.excluded],
                }
                for position in inputs.positions
            ],
            "view": {"overlapping": inputs.overlapping},
        }


def groves(echo: Echo | None = None) -> PackRegistry:
    pack = Pack(
        manifest=PackManifest(
            id="groves", version="1.0.0", results_version=1, requires_core=">=0.0.1"
        ),
        analyses=[echo or Echo(entry())],
    )
    return PackRegistry([pack], core_version=aibi.__version__)


def document(
    cohorts: Sequence[str] = ("apple", "pear"), columns: Mapping[str, Any] = COLUMNS, **view: Any
) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "trees",
        "cohorts": {"apple": {"all": [APPLE]}, "pear": {"all": [PEAR]}, "every": {"all": []}},
        "views": [
            {
                "analysis": "groves.echo",
                "cohorts": list(cohorts),
                "params": {"columns": dict(columns)},
                **view,
            }
        ],
    }


def catalog_of(world: World, registry: PackRegistry, **given: Any) -> Catalog:
    return Catalog(world.store, workers=WORKERS, registry=registry, **given)


def answer(catalog: Catalog, name: str, body: JsonValue) -> Output | list[Refusal]:
    return call(catalog, BY_NAME[name], json.dumps(body).encode())


def run(catalog: Catalog, written: Mapping[str, Any]) -> AnalysisResults:
    found = answer(catalog, "run_analysis", {"document": dict(written)})
    assert isinstance(found, AnalysisResults), found
    return found


def refused(found: Output | list[Refusal]) -> Refusal:
    assert isinstance(found, list), found
    return found[0]


def checked_views(
    world: World, manifest: str, written: Mapping[str, Any], registry: PackRegistry
) -> list[views.CheckedView]:
    """The document's views as phase 2 checks them."""
    analyses = Analyses(registry)
    release = world.store.load(manifest)
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    parsed, refusals = views.parse(loaded.document, loaded.positions, analyses)
    assert refusals == []
    canonical = canonicalise(
        loaded.document,
        {written["dataset"]: release},
        labels={manifest: 1},
        registry=registry,
        positions=loaded.positions,
        variables=[v for view in parsed for v in view.variables],
    )
    found, wrong = views.checked(loaded.document, parsed, canonical, analyses)
    assert wrong == []
    return found


def evaluated(
    world: World, manifest: str, written: Mapping[str, Any], registry: PackRegistry
) -> list[ResultEnvelope]:
    """The document's views of packs' analyses run on inputs the reference evaluator lists."""
    analyses = Analyses(registry)
    found: list[ResultEnvelope] = []
    for view in checked_views(world, manifest, written, registry):
        variables = [variable.resolved for variable in view.variables]
        given = [ordered(listed(cohort.resolved, variables)) for cohort in view.cohorts]
        analysis, _, returns = analyses.implementation(view.analysis.id)
        assert isinstance(view.params, PackParams)
        outcome = packs.run_pack(
            analysis,
            returns,
            [CohortAt(c, evaluate(c.resolved)) for c in view.cohorts],
            list(zip(view.roles, view.variables, strict=True)),
            given,
            view.params,
            reference=view.reference,
            overlapping=bool(shared(given)),
            computation=view.identity.computation_id,
        )
        found.append(
            envelope(
                view,
                outcome,
                issuance="iss:01J0000000000000000000000A",
                written=dict(written),
                params={},
                engine="aibi test",
            )
        )
    return found


def test_a_pack_s_analysis_run_by_sql_gives_the_reference_evaluator_s_result(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard(40, harvests=60))
    registry = groves()
    written = document()
    [result] = run(catalog_of(world, registry), written).results
    [expected] = evaluated(world, published.manifest, written, registry)
    assert result.digest == expected.digest
    assert result.derivation.id == expected.derivation.id
    assert result.values == expected.values
    assert result.derivation.packs["groves"].results_version == 1
    first: Any = result.values.positions[0]
    assert first["units"] == result.population[0].n_true
    assert None in first["values"][0]


def test_a_pack_s_issuance_records_its_listing_after_its_cohorts_counts_and_holds_no_value(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world, groves())
    [result] = run(catalog, document(("apple",))).results
    found = answer(catalog, "explain", {"id": result.issuance.id})
    assert isinstance(found, Explanation)
    issuance = found.issuance
    assert issuance is not None
    assert isinstance(issuance.sql, dict)
    queries = cast(list[dict[str, list[str]]], issuance.sql["queries"])
    assert [len(query["statements"]) for query in queries] == [1, 1 + 3]
    recorded = json.dumps(issuance.model_dump(mode="json"))
    assert '"tree1"' not in recorded


def test_views_that_list_the_same_inputs_share_their_run(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    listings: list[int] = []
    given = analyses_module.run_views

    def counted(*args: Any, **kw: Any) -> Any:
        listings.append(len(kw["inputs"]))
        return given(*args, **kw)

    sorted_listings: list[int] = []
    ordering = analyses_module.ordered

    def sorting(*args: Any, **kw: Any) -> Any:
        sorted_listings.append(1)
        return ordering(*args, **kw)

    monkeypatch.setattr(analyses_module, "run_views", counted)
    monkeypatch.setattr(analyses_module, "ordered", sorting)
    written = document()
    written["views"] = written["views"] * 2
    first, second = run(catalog_of(world, groves()), written).results
    assert listings == [1]
    assert len(sorted_listings) == 2
    assert first.digest == second.digest


def test_cohorts_that_share_units_refuse_an_analysis_that_assumes_independent_groups(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    registry = groves(Echo(entry(independent=True)))
    written = document(("apple", "every"))
    refusal = refused(answer(catalog_of(world, registry), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.COHORTS_OVERLAP, "/views/0")
    assert len(refusal.counts or []) == 1
    allowed = document(("apple", "every"), overlap="allow")
    [result] = run(catalog_of(world, registry), allowed).results
    assert result.values.view["overlapping"] is True


def test_a_cohort_with_more_members_than_a_listing_reads_refuses_the_call(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    monkeypatch.setattr(sql_module, "MAX_LISTED", 3)
    written = document()
    written["views"].insert(0, {"analysis": "summary.members", "cohorts": ["apple"]})
    refusal = refused(answer(catalog_of(world, groves()), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.LIMIT_EXCEEDED, "/views/1")
    assert refusal.limit == Limit(name="listed_members", max=3)


def test_inputs_of_more_cells_than_a_view_may_have_refuse_the_call(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    monkeypatch.setattr(packs, "MAX_INPUT_CELLS", 5)
    refusal = refused(answer(catalog_of(world, groves()), "run_analysis", {"document": document()}))
    assert (refusal.code, refusal.path) == (RefusalCode.LIMIT_EXCEEDED, "/views/0")
    assert refusal.limit is not None
    assert refusal.limit.name == "input_cells"


def test_a_pack_that_fails_refuses_the_call_at_the_view_s_analysis(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())

    class Failing(Echo):
        def run(self, inputs: AnalysisInputs) -> Mapping[str, JsonValue]:
            raise ValueError("tree1")

    registry = groves(Failing(entry()))
    refusal = refused(answer(catalog_of(world, registry), "run_analysis", {"document": document()}))
    assert (refusal.code, refusal.path) == (RefusalCode.PACK_FAILED, "/views/0/analysis")
    assert "tree1" not in json.dumps(refusal.model_dump(mode="json"))


def test_the_pack_runs_by_the_call_s_deadline_less_the_time_to_record(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    seen: list[float | None] = []
    running = packs.run_pack

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("ends"))
        return running(*args, **kwargs)

    monkeypatch.setattr(analyses_module.packs, "run_pack", spy)
    deadline = Deadline(time.monotonic() + 20, 30.0)
    body: JsonValue = {"document": document()}
    found = within(deadline, lambda: answer(catalog_of(world, groves()), "run_analysis", body))
    assert not isinstance(found, list)
    assert seen == [deadline.at - RECORD_SECONDS]


@pytest.mark.parametrize("floor", [2, 3, 5])
def test_under_a_floor_no_tool_runs_a_pack_s_analysis_or_names_a_value(
    world: World, orchard: Orchard, floor: int
) -> None:
    world.publish("orchard", orchard())
    echo = Echo(entry())
    catalog = catalog_of(world, groves(echo), floor=floor)
    for tool in ("validate_document", "count_cohort", "run_analysis"):
        found = answer(catalog, tool, {"document": document()})
        dumped = json.dumps(
            [refusal.model_dump(mode="json") for refusal in found]
            if isinstance(found, list)
            else found.model_dump(mode="json")
        )
        assert "NOT_SUPPORTED" in dumped
        assert '"/views/0/analysis"' in dumped
        assert '"tree1"' not in dumped
    assert echo.handed == []
    found = answer(catalog, "list_analyses", {"dataset": "orchard"})
    assert isinstance(found, AnalysisListing)
    assert found.applicable is not None
    [item] = [a for a in found.applicable if a.analysis == "groves.echo"]
    assert (item.status, item.missing) == ("unavailable", ["min_cell_count"])


def test_inputs_of_more_cells_than_a_view_may_have_are_known_from_the_listing_s_first_rows(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    monkeypatch.setattr(sql_module, "MAX_INPUT_CELLS", 5)
    echo = Echo(entry())
    written = document()
    refusal = refused(
        answer(catalog_of(world, groves(echo)), "run_analysis", {"document": written})
    )
    assert (refusal.code, refusal.path) == (RefusalCode.LIMIT_EXCEEDED, "/views/0")
    assert refusal.limit == Limit(name="input_cells", max=5)
    assert echo.handed == []


def test_views_of_the_same_cohorts_that_read_other_columns_are_each_handed_their_own(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = world.publish("orchard", orchard(30, harvests=45))
    listings: list[int] = []
    given = analyses_module.run_views

    def counted(*args: Any, **kw: Any) -> Any:
        listings.append(len(kw["inputs"]))
        return given(*args, **kw)

    monkeypatch.setattr(analyses_module, "run_views", counted)
    registry = groves()
    written = document()
    other = document(columns={"measure": [{"column": "harvests.kg", "aggregate": "max"}]})
    written["views"] += other["views"]
    found = run(catalog_of(world, registry), written).results
    expected = evaluated(world, published.manifest, written, registry)
    assert listings == [2]
    assert [result.digest for result in found] == [result.digest for result in expected]
    first, second = (cast(Any, result.values.positions[0]) for result in found)
    assert (len(first["values"]), len(second["values"])) == (3, 1)


def test_cohorts_that_share_units_are_handed_as_overlapping_to_an_analysis_that_allows_it(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    [result] = run(catalog_of(world, groves()), document(("apple", "every"))).results
    assert result.values.view["overlapping"] is True
    assert all(caveat.code != "COHORTS_OVERLAP" for caveat in result.caveats)
    [apart] = run(catalog_of(world, groves()), document(("apple", "pear"))).results
    assert apart.values.view["overlapping"] is False


def test_the_server_reads_a_listing_of_inputs_by_the_call_s_deadline(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    seen: list[float | None] = []
    reading = sql_module.CompiledInputs.read

    def spy(self: Any, answers: Any, ends: float | None = None) -> Any:
        seen.append(ends)
        return reading(self, answers, ends)

    monkeypatch.setattr(sql_module.CompiledInputs, "read", spy)
    deadline = Deadline(time.monotonic() + 20, 30.0)
    body: JsonValue = {"document": document()}
    found = within(deadline, lambda: answer(catalog_of(world, groves()), "run_analysis", body))
    assert not isinstance(found, list)
    assert len(seen) == 1
    assert seen[0] is not None
    assert seen[0] <= deadline.at


def test_a_listing_of_inputs_over_its_cap_names_the_first_view_that_reads_it(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    monkeypatch.setattr(sql_module, "MAX_LISTED", 10)
    written = document(("apple",))
    written["views"] += document(("every",))["views"]
    refusal = refused(answer(catalog_of(world, groves()), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.LIMIT_EXCEEDED, "/views/1")
    assert refusal.limit == Limit(name="listed_members", max=10)


def test_a_run_over_its_limits_names_a_view_of_a_pack_s_analysis_before_a_materialisation(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard())
    written = document()
    distribution = {
        "analysis": "summary.distribution",
        "cohorts": ["apple", "pear"],
        "params": {"columns": [{"column": "trees.height_m"}] * 3},
    }
    written["views"].insert(0, distribution)
    found = checked_views(world, published.manifest, written, groves())
    assert analyses_module._widest(found).index == 1  # pyright: ignore[reportPrivateUsage]


def test_positions_whose_members_together_pass_the_cells_cap_are_refused_before_their_rows(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eight apple trees and eight pear trees, each within 36 cells of three columns, and
    together over them (D342)."""
    world.publish("orchard", orchard())
    monkeypatch.setattr(sql_module, "MAX_INPUT_CELLS", 36)
    echo = Echo(entry())
    written = document()
    refusal = refused(
        answer(catalog_of(world, groves(echo)), "run_analysis", {"document": written})
    )
    assert refusal.limit == Limit(name="input_cells", max=36)
    assert echo.handed == []
    monkeypatch.setattr(sql_module, "MAX_INPUT_CELLS", 48)
    [result] = run(catalog_of(world, groves(echo)), written).results
    assert [position["units"] for position in cast(Any, result.values.positions)] == [8, 8]


def test_a_listing_two_views_share_over_its_cap_names_the_first_of_them(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    monkeypatch.setattr(sql_module, "MAX_LISTED", 10)
    written = document(("every",))
    written["views"] = written["views"] * 2
    refusal = refused(answer(catalog_of(world, groves()), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.LIMIT_EXCEEDED, "/views/0")

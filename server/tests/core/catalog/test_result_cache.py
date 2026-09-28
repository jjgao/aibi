"""``run_analysis`` and the result cache (SPEC §8.1, §12.2; D376): a result run again is given
from the cache, rendered for its own call and naming the issuance whose queries gave its values;
a result that lists or hands its members' values, or reads a draft, never is; a hit whose source
or cohorts the log no longer holds as it was found runs again as a miss."""

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest
from pydantic import JsonValue

import aibi
from aibi.core.catalog import analyses as analyses_module
from aibi.core.catalog.cohorts import ENGINE
from aibi.core.catalog.service import Catalog
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.engine.worker import Workers
from aibi.core.schema.cohorts import AnalysisResults, Explanation, RunAnalysis
from aibi.core.schema.descriptors import AnalysisDescriptor
from aibi.core.schema.limits import QueryLimits
from aibi.core.schema.output import Output
from aibi.core.schema.pack_api import AnalysisInputs, Pack, PackManifest, PackRegistry
from aibi.core.schema.refusals import Refusal
from aibi.core.schema.results import ResultEnvelope
from aibi.core.store import redaction
from aibi.core.store.derivations import DerivationConflictError
from aibi.core.store.erasure import erase

World = Any
Orchard = Callable[..., dict[str, bytes]]
ADA = "operator:Ada"
WORKERS = Workers(QueryLimits())
APPLE = {"kind": "value", "column": "trees.variety", "values": ["apple"]}
PEAR = {"kind": "value", "column": "trees.variety", "values": ["pear"]}
TALL = {"kind": "value", "column": "trees.height_m", "range": {"gte": 5}}
HEAVY = {
    "kind": "exists",
    "table": "harvests",
    "where": [{"kind": "value", "column": "harvests.kg", "range": {"gte": 15}}],
}
EXISTENCE = {
    "analysis": "compare.existence",
    "cohorts": ["apple", "pear"],
    "params": {"predicates": [TALL, HEAVY]},
}
DISTRIBUTION = {
    "analysis": "summary.distribution",
    "cohorts": ["apple", "pear"],
    "params": {"columns": [{"column": "trees.height_m"}]},
}


def document(*views: Mapping[str, Any], names: Sequence[str] = ("apple", "pear")) -> dict[str, Any]:
    renamed = dict(zip(("apple", "pear"), names, strict=True))
    return {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "trees",
        "cohorts": {renamed["apple"]: {"all": [APPLE]}, renamed["pear"]: {"all": [PEAR]}},
        "views": [
            {**view, "cohorts": [renamed[name] for name in view["cohorts"]]}
            for view in (views or (EXISTENCE,))
        ],
    }


def catalog_of(world: World, **given: Any) -> Catalog:
    return Catalog(world.store, workers=given.pop("workers", WORKERS), **given)


def answer(catalog: Catalog, name: str, body: JsonValue) -> Output | list[Refusal]:
    return call(catalog, BY_NAME[name], json.dumps(body).encode())


def run(catalog: Catalog, written: Mapping[str, Any]) -> list[ResultEnvelope]:
    found = answer(catalog, "run_analysis", {"document": dict(written)})
    assert isinstance(found, AnalysisResults), found
    return list(found.results)


def workers_run(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """One entry for each worker run of the calls that follow."""
    runs: list[int] = []
    given = analyses_module.run_views

    def counted(*args: Any, **kw: Any) -> Any:
        runs.append(1)
        return given(*args, **kw)

    monkeypatch.setattr(analyses_module, "run_views", counted)
    return runs


def without_issuance(result: ResultEnvelope) -> dict[str, Any]:
    dumped = result.model_dump(mode="json")
    del dumped["issuance"]
    return dumped


def test_a_result_run_again_is_given_from_the_cache_without_running_a_query(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    [first] = run(catalog, document())
    runs = workers_run(monkeypatch)
    [second] = run(catalog, document())
    assert runs == []
    assert second.issuance.cache_hit
    assert second.issuance.values_from == first.issuance.id != second.issuance.id
    assert without_issuance(second) == without_issuance(first)
    explained = answer(catalog, "explain", {"id": second.issuance.id})
    assert isinstance(explained, Explanation)
    assert explained.issuance is not None
    assert explained.issuance.sql is None
    assert explained.issuance.values_from == first.issuance.id


def test_a_hit_is_rendered_with_its_own_call_s_names_and_document(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    [first] = run(catalog, document(DISTRIBUTION))
    written = document(DISTRIBUTION, names=("a", "p"))
    [second] = run(catalog, written)
    assert second.issuance.cache_hit
    assert (second.derivation.id, second.digest) == (first.derivation.id, first.digest)
    assert [label.data for label in second.labels] == ["a", "p"]
    assert second.source.document == written
    assert json.dumps(second.charts) != json.dumps(first.charts)
    assert second.values == first.values


def test_a_call_whose_views_all_hit_but_one_runs_only_that_one(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    run(catalog, document(EXISTENCE))
    runs = workers_run(monkeypatch)
    hit, miss = run(catalog, document(EXISTENCE, DISTRIBUTION))
    assert runs == [1]
    assert hit.issuance.cache_hit
    assert not miss.issuance.cache_hit
    [again] = run(catalog, document(DISTRIBUTION))
    assert again.issuance.values_from == miss.issuance.id


def test_two_views_of_one_result_fill_one_row_and_each_hit_names_it(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    first, twin = run(catalog, document(EXISTENCE, EXISTENCE))
    assert not first.issuance.cache_hit
    assert not twin.issuance.cache_hit
    one, two = run(catalog, document(EXISTENCE, EXISTENCE))
    assert one.issuance.values_from == two.issuance.values_from == first.issuance.id


@pytest.mark.parametrize(
    "view",
    [
        {"analysis": "summary.members", "cohorts": ["apple"], "params": {"limit": 10}},
    ],
    ids=["summary.members"],
)
def test_a_result_that_lists_members_is_never_given_from_the_cache(
    world: World, orchard: Orchard, view: dict[str, Any]
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    written = document(view)
    run(catalog, written)
    [again] = run(catalog, written)
    assert not again.issuance.cache_hit
    assert world.store.results.usage() == 0


class Echo:
    """A pack's analysis whose values are its inputs' units."""

    @property
    def entry(self) -> AnalysisDescriptor:
        return AnalysisDescriptor.model_validate(
            {
                "kind": "analysis",
                "id": "echoes.echo",
                "version": "1.0.0",
                "label": "Echo",
                "fields": {
                    "requires": [
                        {"role": "cohorts", "min": 1, "max": 6},
                        {"role": "measure", "kind": "column", "min": 1},
                    ],
                    "params": {"type": "object"},
                    "returns": {"type": "object"},
                    "methods": {},
                    "assumptions": [],
                    "uses_reference": False,
                    "assumes_independent_groups": False,
                    "cross_dataset": None,
                    "caveats": [],
                },
            }
        )

    def run(self, inputs: AnalysisInputs) -> Mapping[str, JsonValue]:
        return {
            "positions": [{"units": position.units} for position in inputs.positions],
            "view": {},
        }


def test_a_pack_s_result_is_never_given_from_the_cache(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    pack = Pack(
        manifest=PackManifest(
            id="echoes", version="1.0.0", results_version=1, requires_core=">=0.0.1"
        ),
        analyses=[Echo()],
    )
    registry = PackRegistry([pack], core_version=aibi.__version__)
    catalog = catalog_of(world, registry=registry)
    view = {
        "analysis": "echoes.echo",
        "cohorts": ["apple"],
        "params": {"columns": {"measure": [{"column": "trees.height_m"}]}},
    }
    run(catalog, document(view))
    [again] = run(catalog, document(view))
    assert not again.issuance.cache_hit
    assert world.store.results.usage() == 0


def test_a_result_is_neither_given_nor_filled_when_the_catalogue_turns_the_cache_off(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    off = catalog_of(world, cache=False)
    run(off, document())
    assert world.store.results.usage() == 0
    on = catalog_of(world)
    [first] = run(on, document())
    [second] = run(off, document())
    assert not second.issuance.cache_hit
    assert not first.issuance.cache_hit


def test_a_raised_floor_gives_another_id_and_so_no_hit(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    run(catalog_of(world), document())
    [under] = run(catalog_of(world, floor=3), document())
    assert not under.issuance.cache_hit
    assert world.store.results.purge(3) == 1


def test_a_hit_at_other_threads_gives_the_filler_s_content(world: World, orchard: Orchard) -> None:
    """The thread-count determinism tests turn the cache off (D372): a catalogue with it on,
    whose workers differ only in their threads, is given the other's content."""
    world.publish("orchard", orchard())
    [first] = run(catalog_of(world), document(DISTRIBUTION))
    four = Workers(QueryLimits(query_threads=4))
    [second] = run(catalog_of(world, workers=four), document(DISTRIBUTION))
    assert second.issuance.values_from == first.issuance.id


def test_a_hit_whose_source_is_pruned_after_it_is_found_runs_again_as_a_miss(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    run(catalog, document())
    given = world.store.results.lookup
    found: list[Any] = []

    def then_pruned(*args: Any) -> Any:
        row = given(*args)
        if row is not None and not found:
            found.append(row)
            world.store.derivations.prune(world.store.now())
        return row

    monkeypatch.setattr(world.store.results, "lookup", then_pruned)
    [again] = run(catalog, document())
    assert found
    assert not again.issuance.cache_hit


def test_a_hit_whose_row_is_evicted_after_it_is_found_is_still_a_hit(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    [first] = run(catalog, document())
    given = world.store.results.lookup

    def then_cleared(*args: Any) -> Any:
        row = given(*args)
        world.store.results.clear()
        return row

    monkeypatch.setattr(world.store.results, "lookup", then_cleared)
    [again] = run(catalog, document())
    assert again.issuance.values_from == first.issuance.id


def without_tree1(files: Mapping[str, bytes]) -> dict[str, bytes]:
    """The orchard without ``tree1`` and its harvests."""
    return {
        name: b"\n".join(
            line
            for line in content.split(b"\n")
            if not line.startswith(b"tree1,") and b",tree1," not in line
        )
        for name, content in files.items()
    }


def test_a_hit_whose_cohort_erasure_took_is_never_given(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Erasure empties the cache (D375). Were a row to outlive it over a release that holds no
    one erased, a hit whose cohort's derivation erasure took is not given: its transaction finds
    the cohort erased, and the call runs again without the cache, as a first run of the document
    would (D376)."""
    world.publish("orchard", orchard())
    world.reimport("orchard", without_tree1(orchard()))
    named = {"kind": "value", "column": "trees.tree_id", "values": ["tree1"]}
    written = document()
    written["cohorts"]["apple"]["all"] = [APPLE, {"not": named}]
    catalog = catalog_of(world)
    run(catalog, written)
    monkeypatch.setitem(redaction.REDACTORS, "result_cache", lambda db, dataset, terms: 0)
    assert erase(world.store, "orchard", "trees", ["tree1"], ADA).redacted
    kept = world.store.db.connection.execute("SELECT count(*) FROM result_cache").fetchone()[0]
    assert kept == 1
    request = RunAnalysis.model_validate({"document": written})
    with pytest.raises(analyses_module._StaleHit):  # pyright: ignore[reportPrivateUsage]
        analyses_module._analysed(catalog, request, cached=True)  # pyright: ignore[reportPrivateUsage]


def test_a_hit_the_cache_still_gives_after_its_source_is_gone_is_run_without_the_cache(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The call runs again without looking the cache up, so a row that outlived its filler
    never gives the call a second stale hit (D376)."""
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    [first] = run(catalog, document())
    stale = world.store.results.lookup(first.derivation.id, ENGINE, {})
    assert stale is not None
    assert world.store.derivations.prune(world.store.now()) > 0
    monkeypatch.setattr(world.store.results, "lookup", lambda *args: stale)
    [again] = run(catalog, document())
    assert not again.issuance.cache_hit
    assert again.digest == first.digest


@pytest.mark.xfail(
    strict=True,
    raises=DerivationConflictError,
    reason="#31's follow-up: a document whose cohort's derivation erasure took is re-recorded",
)
def test_a_call_whose_hit_names_a_cohort_erasure_took_is_answered_as_a_first_run_is(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the tool, the hit runs again as a miss (D376); that first run itself raises until
    the follow-up, so this fails for that alone, and passes once it is fixed."""
    world.publish("orchard", orchard())
    world.reimport("orchard", without_tree1(orchard()))
    named = {"kind": "value", "column": "trees.tree_id", "values": ["tree1"]}
    written = document()
    written["cohorts"]["apple"]["all"] = [APPLE, {"not": named}]
    catalog = catalog_of(world)
    run(catalog, written)
    monkeypatch.setitem(redaction.REDACTORS, "result_cache", lambda db, dataset, terms: 0)
    assert erase(world.store, "orchard", "trees", ["tree1"], ADA).redacted
    [again] = run(catalog, written)
    assert not again.issuance.cache_hit


def test_a_row_that_does_not_read_back_for_its_view_is_a_miss_the_call_replaces(
    world: World, orchard: Orchard, caplog: pytest.LogCaptureFixture
) -> None:
    """Logged by its kind alone, and filled again with this call's content, so it is not taken
    for a second fill of the same id with another content (§7.6)."""
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    [first] = run(catalog, document())
    [other] = run(catalog, document(DISTRIBUTION))
    wrong = world.store.results.lookup(other.derivation.id, ENGINE, {})
    assert wrong is not None
    world.store.results.clear()
    with world.store.db.transaction() as db:
        assert world.store.results.fill(
            db, result=first.derivation.id, issuance=first.issuance.id, content=wrong.content
        )
    with caplog.at_level(logging.ERROR):
        [again] = run(catalog, document())
    assert not again.issuance.cache_hit
    assert [record.getMessage() for record in caplog.records] == [
        "result cache: a cached content did not read back for its view"
    ]
    [third] = run(catalog, document())
    assert third.issuance.values_from == again.issuance.id


def test_a_hit_marks_its_row_used_after_every_other(world: World, orchard: Orchard) -> None:
    """So eviction, which takes the least recently used rows, takes it last (D375)."""
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    [first] = run(catalog, document())
    run(catalog, document(DISTRIBUTION))
    run(catalog, document())
    with world.store.db.lock:
        [latest] = world.store.db.connection.execute(
            "SELECT result FROM result_cache ORDER BY used DESC LIMIT 1"
        ).fetchone()
    assert latest == first.derivation.id

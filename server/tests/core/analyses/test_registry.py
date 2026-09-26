"""The analysis registry, its extension points and applicability (SPEC §9.1, §9.4, §10.1;
D316), with a test-only pack, ``shelves``, that registers an analysis and a requirement
predicate."""

import json
from collections.abc import Callable, Mapping
from typing import Any

import pytest
from pydantic import JsonValue

import aibi
from aibi.core.analyses.existence import ENTRY, METHODS
from aibi.core.analyses.registry import Analyses
from aibi.core.schema.descriptors import AnalysisDescriptor
from aibi.core.schema.pack_api import AnalysisInputs, Pack, PackManifest, PackRegistry, ReleaseView
from aibi.core.schema.refusals import RefusalCode

Check = Callable[..., Any]
Shop = Callable[..., Any]


def entry(requires: list[dict[str, Any]]) -> AnalysisDescriptor:
    return AnalysisDescriptor.model_validate(
        {
            "kind": "analysis",
            "id": "shelves.restock",
            "version": "0.1.0",
            "label": "Restocking",
            "fields": {
                "requires": requires,
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


class Restock:
    def __init__(self, requires: list[dict[str, Any]]) -> None:
        self._entry = entry(requires)

    @property
    def entry(self) -> AnalysisDescriptor:
        return self._entry

    def run(self, inputs: AnalysisInputs) -> Mapping[str, JsonValue]:
        raise AssertionError("the core runs no pack's analysis until M3.2d")


def stocked(release: ReleaseView) -> bool:
    return "orders" in release.descriptors


def failing(release: ReleaseView) -> bool:
    raise RuntimeError("a predicate that fails")


def shelves(requires: list[dict[str, Any]]) -> PackRegistry:
    pack = Pack(
        manifest=PackManifest(
            id="shelves", version="1.0.0", results_version=1, requires_core=">=0.0.1"
        ),
        analyses=[Restock(requires)],
        requirement_predicates={"stocked": stocked, "failing": failing},
    )
    return PackRegistry([pack], core_version=aibi.__version__)


def statuses(analyses: Analyses, release: Any, unit: str | None = None) -> dict[str, Any]:
    found = analyses.applicable(
        list(release.descriptors), dataset=release.dataset, manifest=release.manifest, unit=unit
    )
    return {item.analysis: (item.status, item.missing, item.unconfirmed) for item in found}


def test_the_core_s_entry_is_generated_from_its_models() -> None:
    fields = ENTRY.fields
    assert ENTRY.id == "compare.existence"
    assert set(fields.methods) == set(METHODS)
    assert fields.params["properties"]["predicates"]["maxItems"] == 16  # type: ignore[index]
    assert "ExistencePosition" in fields.returns["$defs"]  # type: ignore[operator]
    assert fields.uses_reference
    assert fields.assumes_independent_groups
    assert fields.cross_dataset is None


def test_the_registry_holds_the_core_s_analyses_and_the_packs_by_id() -> None:
    analyses = Analyses(shelves([{"role": "orders", "kind": "table"}]))
    assert analyses.ids() == [
        "compare.existence",
        "shelves.restock",
        "summary.distribution",
        "summary.members",
    ]
    found = analyses.get("shelves.restock")
    assert found is not None
    assert found.pack == "shelves"
    assert found.params is None
    assert analyses.get("compare.existence") is not None
    assert analyses.get("shelves.other") is None
    assert analyses.get("elsewhere.restock") is None
    assert analyses.get("compare.columns") is None


def test_an_entry_handed_out_cannot_change_the_registry() -> None:
    analyses = Analyses()
    found = analyses.get("compare.existence")
    assert found is not None
    found.entry.fields.methods.clear()
    again = analyses.get("compare.existence")
    assert again is not None
    assert again.entry.fields.methods


@pytest.mark.parametrize(
    ("requires", "status", "missing"),
    [
        ([{"role": "orders", "kind": "table"}], "available", []),
        ([{"role": "when", "kind": "endpoint"}], "unavailable", ["when"]),
        ([{"role": "when", "kind": "endpoint", "min": 0}], "available", []),
        (
            [{"role": "ages", "kind": "column", "datatype": "integer", "on": "unit"}],
            "available",
            [],
        ),
        ([{"role": "totals", "kind": "column", "datatype": "number"}], "unavailable", ["totals"]),
        ([{"role": "stock", "predicate": "shelves.stocked"}], "available", []),
        ([{"role": "stock", "predicate": "shelves.failing"}], "unavailable", ["stock"]),
        ([{"role": "stock", "predicate": "shelves.missing"}], "unavailable", ["stock"]),
        ([{"role": "cohorts", "min": 1, "max": 2}], "available", []),
    ],
)
def test_applicability_matches_each_requirement_against_the_release(
    shop: Shop, requires: list[dict[str, Any]], status: str, missing: list[str]
) -> None:
    found = statuses(Analyses(shelves(requires)), shop(), unit="customers")
    assert found["shelves.restock"] == (status, missing, [])
    assert found["compare.existence"] == ("available", [], [])


def test_a_requirement_met_only_by_unconfirmed_descriptors_is_available_with_caveats(
    shop: Shop,
) -> None:
    release = shop()
    descriptors = list(release.descriptors)
    for index, descriptor in enumerate(descriptors):
        if descriptor.id == "orders.channel":
            written = descriptor.model_dump(mode="json")
            written["curation"]["/fields/datatype"]["status"] = "proposed"
            written["curation"]["/fields/datatype"]["by"] = "agent:someone"
            descriptors[index] = type(descriptor).model_validate(written)
    analyses = Analyses(shelves([{"role": "kinds", "kind": "column", "datatype": "category"}]))
    only = [d for d in descriptors if d.id != "customers.tier" and d.id != "returns.reason"]
    found = analyses.applicable(only, dataset="d", manifest=release.manifest, unit="customers")
    [restock] = [item for item in found if item.analysis == "shelves.restock"]
    assert (restock.status, restock.unconfirmed) == ("available_with_caveats", ["kinds"])


def _unconfirmed(descriptors: list[Any], ids: set[str]) -> list[Any]:
    found = []
    for descriptor in descriptors:
        if descriptor.id in ids:
            written = descriptor.model_dump(mode="json")
            written["curation"]["/fields/datatype"]["status"] = "proposed"
            written["curation"]["/fields/datatype"]["by"] = "agent:someone"
            descriptor = type(descriptor).model_validate(written)
        found.append(descriptor)
    return found


@pytest.mark.parametrize(("least", "status"), [(1, "available"), (2, "available_with_caveats")])
def test_a_requirement_needs_its_min_confirmed_descriptors_to_be_available(
    shop: Shop, least: int, status: str
) -> None:
    release = shop()
    descriptors = _unconfirmed(list(release.descriptors), {"orders.channel", "returns.reason"})
    requires = [{"role": "kinds", "kind": "column", "datatype": "category", "min": least}]
    found = statuses(Analyses(shelves(requires)), release, unit="customers")
    assert found["shelves.restock"][0] == "available"
    listed = Analyses(shelves(requires)).applicable(
        descriptors, dataset="d", manifest=release.manifest, unit="customers"
    )
    [restock] = [item for item in listed if item.analysis == "shelves.restock"]
    assert restock.status == status


def test_a_requirement_predicate_runs_once_for_every_keyed_table(shop: Shop) -> None:
    calls: list[str] = []

    def counted(release: ReleaseView) -> bool:
        calls.append(release.dataset)
        return True

    pack = Pack(
        manifest=PackManifest(
            id="shelves", version="1.0.0", results_version=1, requires_core=">=0.0.1"
        ),
        analyses=[Restock([{"role": "stock", "predicate": "shelves.counted"}])],
        requirement_predicates={"counted": counted},
    )
    analyses = Analyses(PackRegistry([pack], core_version=aibi.__version__))
    assert statuses(analyses, shop())["shelves.restock"] == ("available", [], [])
    assert len(calls) == 1


def test_without_a_unit_each_keyed_table_is_tried_and_the_best_kept(shop: Shop) -> None:
    analyses = Analyses(
        shelves([{"role": "own", "kind": "column", "datatype": "category", "on": "unit"}])
    )
    assert statuses(analyses, shop())["shelves.restock"] == ("available", [], [])
    assert statuses(analyses, shop(), unit="checked_orders")["shelves.restock"] == (
        "unavailable",
        ["own"],
        [],
    )


def test_a_view_of_a_pack_s_analysis_is_listed_but_not_run(check: Check, shop: Shop) -> None:
    written = {
        "aibi": "1",
        "dataset": "d",
        "unit": "customers",
        "cohorts": {"all": {"all": []}},
        "views": [{"analysis": "shelves.restock"}],
    }
    found = check(written, shop(), analyses=Analyses(shelves([])))
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (RefusalCode.NOT_SUPPORTED, "/views/0/analysis")
    assert "M3.2d" in json.dumps([part.model_dump() for part in refusal.message])

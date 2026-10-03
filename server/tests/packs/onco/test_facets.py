"""The pack's catalogue facet (SPEC §10.1, §11.1, D271, D273): declared extension members alone,
for a dataset that lists the pack; each facet a non-empty list, a facet with no value left out;
and the facets in the catalogue, once a dataset that lists the pack is curated to declare them."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import JsonValue

from aibi.core.catalog import index
from aibi.core.engine import build
from aibi.core.schema.catalog import SearchCatalog
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.pack_api import PackRegistry

Extended = Callable[[Descriptor, Mapping[str, JsonValue]], Descriptor]
MANIFEST = "sha256:" + "0" * 64


def _facets(
    registry: PackRegistry, descriptors: Sequence[Descriptor]
) -> dict[str, tuple[str, ...]]:
    return index.facets(registry, "d", MANIFEST, 1, descriptors)


def _release(extended: Extended, *, listed: bool = True, **declared: Any) -> list[Descriptor]:
    dataset = build.dataset(packs=["onco"]) if listed else build.dataset()
    members = {name: value for name, value in declared.items() if name != "metas"}
    found: list[Descriptor] = [extended(dataset, members) if members else dataset]
    for at, meta in enumerate(declared.get("metas", ())):
        table = build.table(f"t{at}", key=["k"])
        found.append(extended(table, {"meta": meta}) if meta is not None else table)
        found.append(build.column(f"t{at}.k", "string"))
    return found


def test_the_facets_of_a_dataset_that_lists_the_pack(
    registry: PackRegistry, extended: Extended
) -> None:
    descriptors = _release(
        extended,
        type_of_cancer="brca",
        reference_genome="hg38",
        cancer_study_identifier="brca_study",
        metas=[
            "MUTATION_EXTENDED:MAF",
            "COPY_NUMBER_ALTERATION:DISCRETE",
            "MUTATION_EXTENDED:MAF",
            "CLINICAL:SAMPLE_ATTRIBUTES",
            "CLINICAL:PATIENT_ATTRIBUTES",
            "CANCER_TYPE:CANCER_TYPE",
            None,
        ],
    )
    assert _facets(registry, descriptors) == {
        "onco.profiles": ("COPY_NUMBER_ALTERATION:DISCRETE", "MUTATION_EXTENDED:MAF"),
        "onco.reference_genome": ("hg38",),
        "onco.type_of_cancer": ("brca",),
    }


def test_a_dataset_that_does_not_list_the_pack_has_no_facets(
    registry: PackRegistry, extended: Extended
) -> None:
    descriptors = _release(
        extended, listed=False, type_of_cancer="brca", metas=["MUTATION_EXTENDED:MAF"]
    )
    assert _facets(registry, descriptors) == {}


def test_a_facet_with_no_value_is_left_out(registry: PackRegistry, extended: Extended) -> None:
    assert _facets(registry, _release(extended)) == {}
    assert _facets(registry, _release(extended, reference_genome="hg19")) == {
        "onco.reference_genome": ("hg19",)
    }
    clinical = _release(
        extended,
        cancer_study_identifier="s",
        metas=["CLINICAL:SAMPLE_ATTRIBUTES", "CANCER_TYPE:CANCER_TYPE", None],
    )
    assert _facets(registry, clinical) == {}


def test_the_facet_reads_declared_members_alone(onco: Any, extended: Extended) -> None:
    descriptors = _release(extended, type_of_cancer="gbm", metas=["MRNA_EXPRESSION:Z-SCORE"])

    class View:
        dataset = "d"
        manifest = MANIFEST
        label = 1
        packs = ("onco",)

        @property
        def descriptors(self) -> dict[str, Descriptor]:
            return {descriptor.id: descriptor for descriptor in descriptors}

    found = onco.facets.facet(View())
    assert found == {"type_of_cancer": ["gbm"], "profiles": ["MRNA_EXPRESSION:Z-SCORE"]}
    assert type(found) is dict
    assert all(type(values) is list and values for values in found.values())


def test_the_catalogue_shows_the_facets_once_declared(world: Any, registry: PackRegistry) -> None:
    directory = world.write("study", {"samples.csv": b"sample_id,kind\ns1,a\ns2,b\n"})
    world.publish("study", directory, registry)
    hit = world.catalog(registry).search_catalog(SearchCatalog.model_validate({})).hits[0]
    assert hit.facets == {}
    world.curate(
        "study",
        registry,
        {"op": "set", "descriptor": "dataset", "pointer": "/fields/packs", "value": ["onco"]},
        *(
            {"op": "set", "descriptor": id, "pointer": f"/extensions/onco/{name}", "value": value}
            for id, name, value in (
                ("dataset", "type_of_cancer", "brca"),
                ("dataset", "reference_genome", "hg19"),
                ("samples", "meta", "MUTATION_EXTENDED:MAF"),
            )
        ),
    )
    catalog = world.catalog(registry)
    hit = catalog.search_catalog(SearchCatalog.model_validate({})).hits[0]
    assert {name: [value.data for value in values] for name, values in hit.facets.items()} == {
        "onco.profiles": ["MUTATION_EXTENDED:MAF"],
        "onco.reference_genome": ["hg19"],
        "onco.type_of_cancer": ["brca"],
    }
    query = SearchCatalog.model_validate({"facets": {"onco.type_of_cancer": ["brca"]}})
    assert [found.dataset for found in catalog.search_catalog(query).hits] == ["study"]
    other = SearchCatalog.model_validate({"facets": {"onco.type_of_cancer": ["gbm"]}})
    assert catalog.search_catalog(other).hits == []
    assert world.catalog(None).search_catalog(query).hits == []

"""The pack's catalogue facet (SPEC §10.1, §11.1, D271, D273): declared extension members alone,
for a dataset that lists the pack; each facet a non-empty list, a facet with no value left out;
and the facets in the catalogue, once a dataset that lists the pack is curated to declare them."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest
from pydantic import JsonValue

from aibi.core.catalog import index
from aibi.core.engine import build
from aibi.core.schema.catalog import SearchCatalog
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.pack_api import PackRegistry

Extended = Callable[[Descriptor, Mapping[str, JsonValue]], Descriptor]
MANIFEST = "sha256:" + "0" * 64

PROFILES = (
    "PROTEIN_LEVEL:LOG2-VALUE",
    "PROTEIN_LEVEL:Z-SCORE",
    "PROTEIN_LEVEL:CONTINUOUS",
    "COPY_NUMBER_ALTERATION:DISCRETE",
    "COPY_NUMBER_ALTERATION:DISCRETE_LONG",
    "COPY_NUMBER_ALTERATION:CONTINUOUS",
    "COPY_NUMBER_ALTERATION:LOG2-VALUE",
    "MRNA_EXPRESSION:CONTINUOUS",
    "MRNA_EXPRESSION:Z-SCORE",
    "MRNA_EXPRESSION:DISCRETE",
    "MUTATION_EXTENDED:MAF",
    "MUTATION_UNCALLED:MAF",
    "METHYLATION:CONTINUOUS",
    "STRUCTURAL_VARIANT:SV",
    "GENESET_SCORE:GSVA-SCORE",
    "GENESET_SCORE:P-VALUE",
    "GENERIC_ASSAY:LIMIT-VALUE",
    "GENERIC_ASSAY:BINARY",
    "GENERIC_ASSAY:CATEGORICAL",
)
"""The pairs whose meta type has a ``stable_id`` and a ``profile_name`` in cBioPortal's
``META_FIELD_MAP`` (``cbioportal_common.py``): the molecular profiles."""
NOT_PROFILES = (
    "CANCER_TYPE:CANCER_TYPE",
    "CLINICAL:PATIENT_ATTRIBUTES",
    "CLINICAL:SAMPLE_ATTRIBUTES",
    "CLINICAL:TIMELINE",
    "COPY_NUMBER_ALTERATION:SEG",
    "GENE_PANEL_MATRIX:GENE_PANEL_MATRIX",
    "GISTIC_GENES_AMP:Q-VALUE",
    "GISTIC_GENES_DEL:Q-VALUE",
    "MUTSIG:Q-VALUE",
)
"""The other nine of the 28 pairs: meta types with neither."""


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


def test_the_28_pairs_are_the_profiles_and_the_files_that_are_not(onco: Any) -> None:
    assert (len(PROFILES), len(NOT_PROFILES)) == (19, 9)
    assert not set(PROFILES) & set(NOT_PROFILES)
    assert set(PROFILES) | set(NOT_PROFILES) == set(onco.schemas.PAIRS)
    assert len(onco.schemas.PAIRS) == 28


@pytest.mark.parametrize("pair", [*PROFILES, *NOT_PROFILES])
def test_each_pair_is_a_profile_or_not(
    registry: PackRegistry, extended: Extended, pair: str
) -> None:
    found = _facets(registry, _release(extended, metas=[pair]))
    assert found == ({"onco.profiles": (pair,)} if pair in PROFILES else {})


def test_the_profiles_are_sorted_whatever_the_order_they_were_declared_in(
    registry: PackRegistry, extended: Extended
) -> None:
    ordered = sorted(PROFILES)
    assert len(ordered) == 19
    for declared in (ordered[::-1], [*ordered[::-1], *NOT_PROFILES, *ordered]):
        found = _facets(registry, _release(extended, metas=declared))
        assert found == {"onco.profiles": tuple(ordered)}


@pytest.mark.parametrize("stable_id", ["study_mutations", *PROFILES])
def test_a_stable_id_without_a_meta_is_no_profile(
    registry: PackRegistry, extended: Extended, stable_id: str
) -> None:
    """Even a stable id that spells a profile's pair: only ``meta`` names a table's profile."""
    table = extended(build.table("t", key=["k"]), {"stable_id": stable_id})
    descriptors = [extended(build.dataset(packs=["onco"]), {"reference_genome": "hg19"})]
    descriptors += [table, build.column("t.k", "string")]
    assert _facets(registry, descriptors) == {"onco.reference_genome": ("hg19",)}


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

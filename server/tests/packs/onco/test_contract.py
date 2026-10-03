"""The pack's contract, pinned (D391): its manifest, its 15 concepts with their sorts, versions
and value lists, the Sequence Ontology terms its values cite, cBioPortal's 28 ``meta`` pairs and
its extension schemas' members. Concept ids are added, never renamed or removed, and a change to
any of this is made here on purpose, with a pack version bump; a golden that fails is the point.

The citations were checked against the Sequence Ontology's ``so.obo`` (data-version 2026-08-07):
each code names the term given, and none is obsolete. NCIt could not be reached, so no value
cites it.
"""

from types import ModuleType
from typing import Any

from aibi.core.schema.descriptors import ConceptDescriptor
from aibi.core.schema.pack_api import PackRegistry

MANIFEST = {"id": "onco", "version": "0.1.0", "results_version": 1, "requires_core": ">=0.0.1"}

SORTS = {
    "onco:patient": "table",
    "onco:sample": "table",
    "onco:gene": "table",
    "onco:gene_symbol": "value",
    "onco:sample_type": "value",
    "onco:mutation_class": "value",
    "onco:cna_level": "value",
    "onco:endpoint.os": "endpoint",
    "onco:endpoint.pfs": "endpoint",
    "onco:endpoint.dfs": "endpoint",
    "onco:endpoint.dss": "endpoint",
    "onco:origin.diagnosis": "time_origin",
    "onco:origin.specimen_collection": "time_origin",
    "onco:origin.treatment_start": "time_origin",
    "onco:origin.first_sequencing": "time_origin",
}

SAMPLE_TYPES = [
    ("PRIMARY_SOLID_TUMOR", "Primary Solid Tumor"),
    ("RECURRENT_SOLID_TUMOR", "Recurrent Solid Tumor"),
    ("PRIMARY_BLOOD_TUMOR", "Primary Blood Tumor"),
    ("RECURRENT_BLOOD_TUMOR", "Recurrent Blood Tumor"),
    ("METASTATIC", "Metastatic"),
    ("BLOOD_DERIVED_NORMAL", "Blood Derived Normal"),
    ("SOLID_TISSUES_NORMAL", "Solid Tissues Normal"),
]
"""cBioPortal's ``Sample.Type`` (``Sample.java``): its constants and their display strings."""

MUTATION_CLASSES = [
    "Frame_Shift_Del",
    "Frame_Shift_Ins",
    "In_Frame_Del",
    "In_Frame_Ins",
    "Missense_Mutation",
    "Nonsense_Mutation",
    "Splice_Site",
    "Translation_Start_Site",
    "Nonstop_Mutation",
    "Targeted_Region",
    "De_novo_Start_InFrame",
    "De_novo_Start_OutOfFrame",
    "Silent",
    "Intron",
    "3'UTR",
    "3'Flank",
    "5'UTR",
    "5'Flank",
    "IGR",
    "RNA",
    "Splice_Region",
    "Fusion",
    "Unknown",
]
"""cBioPortal's ``VARIANT_CLASSIFICATION_VALUES`` (``validateData.py``), in its order."""

CITED = {
    "Frame_Shift_Del": ("SO:0001589", "frameshift_variant", "broader"),
    "Frame_Shift_Ins": ("SO:0001589", "frameshift_variant", "broader"),
    "In_Frame_Del": ("SO:0001822", "inframe_deletion", "exact"),
    "In_Frame_Ins": ("SO:0001821", "inframe_insertion", "exact"),
    "Missense_Mutation": ("SO:0001583", "missense_variant", "exact"),
    "Nonsense_Mutation": ("SO:0001587", "stop_gained", "exact"),
    "Splice_Site": ("SO:0001629", "splice_site_variant", "broader"),
    "Translation_Start_Site": ("SO:0002012", "start_lost", "exact"),
    "Nonstop_Mutation": ("SO:0001578", "stop_lost", "exact"),
    "Silent": ("SO:0001819", "synonymous_variant", "exact"),
    "Intron": ("SO:0001627", "intron_variant", "exact"),
    "3'UTR": ("SO:0001624", "3_prime_UTR_variant", "exact"),
    "3'Flank": ("SO:0001632", "downstream_gene_variant", "exact"),
    "5'UTR": ("SO:0001623", "5_prime_UTR_variant", "exact"),
    "5'Flank": ("SO:0001631", "upstream_gene_variant", "exact"),
    "IGR": ("SO:0001628", "intergenic_variant", "exact"),
    "RNA": ("SO:0001619", "non_coding_transcript_variant", "broader"),
    "Splice_Region": ("SO:0001630", "splice_region_variant", "exact"),
    "Fusion": ("SO:0001565", "gene_fusion", "exact"),
}
"""The SO term each mutation class cites; the others (``Targeted_Region``, the two de novo
starts and ``Unknown``) cite none."""

CNA_LEVELS = [
    ("-2", "Deep deletion"),
    ("-1", "Shallow deletion"),
    ("0", "Diploid"),
    ("1", "Gain"),
    ("2", "Amplification"),
]

PAIRS = [
    "CANCER_TYPE:CANCER_TYPE",
    "CLINICAL:PATIENT_ATTRIBUTES",
    "CLINICAL:SAMPLE_ATTRIBUTES",
    "CLINICAL:TIMELINE",
    "PROTEIN_LEVEL:LOG2-VALUE",
    "PROTEIN_LEVEL:Z-SCORE",
    "PROTEIN_LEVEL:CONTINUOUS",
    "COPY_NUMBER_ALTERATION:DISCRETE",
    "COPY_NUMBER_ALTERATION:DISCRETE_LONG",
    "COPY_NUMBER_ALTERATION:CONTINUOUS",
    "COPY_NUMBER_ALTERATION:LOG2-VALUE",
    "COPY_NUMBER_ALTERATION:SEG",
    "MRNA_EXPRESSION:CONTINUOUS",
    "MRNA_EXPRESSION:Z-SCORE",
    "MRNA_EXPRESSION:DISCRETE",
    "MUTATION_EXTENDED:MAF",
    "MUTATION_UNCALLED:MAF",
    "METHYLATION:CONTINUOUS",
    "GENE_PANEL_MATRIX:GENE_PANEL_MATRIX",
    "STRUCTURAL_VARIANT:SV",
    "GISTIC_GENES_AMP:Q-VALUE",
    "GISTIC_GENES_DEL:Q-VALUE",
    "MUTSIG:Q-VALUE",
    "GENESET_SCORE:GSVA-SCORE",
    "GENESET_SCORE:P-VALUE",
    "GENERIC_ASSAY:LIMIT-VALUE",
    "GENERIC_ASSAY:BINARY",
    "GENERIC_ASSAY:CATEGORICAL",
]
"""cBioPortal's ``alt_type_datatype_to_meta`` (``cbioportal_common.py``), in its order."""

SCHEMAS: dict[str, dict[str, Any]] = {
    "dataset": {
        "cancer_study_identifier": {"type": "string", "minLength": 1, "maxLength": 200},
        "type_of_cancer": {"type": "string", "minLength": 1, "maxLength": 64},
        "reference_genome": {"enum": ["hg19", "hg38", "mm10"]},
    },
    "table": {
        "meta": {"enum": PAIRS},
        "stable_id": {"type": "string", "minLength": 1, "maxLength": 200},
        "case_list_category": {"type": "string", "minLength": 1, "maxLength": 64},
    },
    "column": {
        "attribute_priority": {"type": "integer", "minimum": -1, "maximum": 2**53 - 1},
    },
}

SYSTEMS = ["HGNC", "NCBIGene", "OncoTree", "SO"]


def _by_id(registry: PackRegistry) -> dict[str, ConceptDescriptor]:
    return {concept.id: concept for concept in registry.concepts()}


def _values(concept: ConceptDescriptor) -> tuple[list[tuple[str, str | None]], bool] | None:
    given = concept.fields.permissible_values
    if given is None:
        return None
    return [(value.value, value.label) for value in given.values], given.ordered


def test_the_manifest(registry: PackRegistry) -> None:
    assert registry.ids == ("onco",)
    assert registry.pack("onco").manifest.model_dump() == MANIFEST


def test_the_concepts_their_sorts_and_versions(registry: PackRegistry) -> None:
    concepts = _by_id(registry)
    assert registry.concept_sorts() == SORTS
    assert sorted(concepts) == sorted(SORTS)
    assert {concept.version for concept in concepts.values()} == {1}
    for concept in concepts.values():
        assert concept.fields.units is None
        assert concept.label
        assert concept.definition
        if concept.id not in ("onco:sample_type", "onco:mutation_class", "onco:cna_level"):
            assert concept.fields.permissible_values is None, concept.id


def test_the_value_lists(registry: PackRegistry) -> None:
    concepts = _by_id(registry)
    assert _values(concepts["onco:sample_type"]) == (SAMPLE_TYPES, False)
    classes = _values(concepts["onco:mutation_class"])
    assert classes is not None
    assert [value for value, _ in classes[0]] == MUTATION_CLASSES
    assert classes[1] is False
    assert _values(concepts["onco:cna_level"]) == (CNA_LEVELS, True)
    assert _values(concepts["onco:gene_symbol"]) is None


def test_each_citation(registry: PackRegistry) -> None:
    found: dict[str, tuple[str, str, str]] = {}
    for concept in registry.concepts():
        given = concept.fields.permissible_values
        for value in () if given is None else given.values:
            for cited in value.concepts or ():
                assert concept.id == "onco:mutation_class"
                assert value.value not in found
                assert cited.system == "SO"
                found[value.value] = (cited.code, cited.label, cited.relation)
    assert found == CITED


def test_the_ontology_systems(onco: ModuleType) -> None:
    assert sorted(onco.PACK.ontology_systems) == SYSTEMS


def test_the_extension_schemas(registry: PackRegistry, onco: ModuleType) -> None:
    pairs: tuple[str, ...] = onco.schemas.PAIRS
    assert pairs == tuple(PAIRS)
    assert len(set(PAIRS)) == 28
    for kind in ("relationship", "coverage", "endpoint"):
        assert registry.extension_schemas(kind, ["onco"]) == {}
    for kind, members in SCHEMAS.items():
        schema = registry.extension_schemas(kind, ["onco"])["onco"]
        assert schema == {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": members,
            "additionalProperties": False,
        }

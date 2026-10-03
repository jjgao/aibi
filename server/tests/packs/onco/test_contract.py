"""The pack's contract, pinned (D391): its manifest, its 15 concepts with their sorts, versions
and value lists, the Sequence Ontology terms its values cite, cBioPortal's 28 ``meta`` pairs and
its extension schemas' members. Concept ids are added, never renamed or removed, and a change to
any of this is made here on purpose, with a pack version bump; a golden that fails is the point.

The citations were checked against the Sequence Ontology's ``so.obo`` (data-version 2026-08-07):
each code names the term given, none is obsolete, and the relations stand to each other as the
classes' disjointness asks (``SO_IS_A`` and ``test_no_class_is_part_of_another``). NCIt could not
be reached, so no value cites it.
"""

from types import ModuleType
from typing import Any

from aibi.core.schema.descriptors import ConceptDescriptor
from aibi.core.schema.pack_api import PackRegistry

MANIFEST = {"id": "onco", "version": "0.1.0", "results_version": 1, "requires_core": ">=0.0.1"}

TEXTS = {
    "onco:patient": ("Patient", "Rows are patients: the people a study follows."),
    "onco:sample": ("Sample", "Rows are samples: specimens taken from patients and profiled."),
    "onco:gene": ("Gene", "Rows are genes."),
    "onco:gene_symbol": ("Gene symbol", "A gene's approved symbol."),
    "onco:sample_type": (
        "Sample type",
        "What a sample is: a primary, recurrent or metastatic tumour, or normal tissue or blood.",
    ),
    "onco:mutation_class": (
        "Mutation class",
        "The class of a mutation call, as a MAF file's Variant_Classification gives it.",
    ),
    "onco:cna_level": (
        "Copy-number level",
        "A discrete copy-number call, from deep deletion to amplification.",
    ),
    "onco:endpoint.os": ("Overall survival", "Time to death from any cause."),
    "onco:endpoint.pfs": ("Progression-free survival", "Time to disease progression or death."),
    "onco:endpoint.dfs": ("Disease-free survival", "Time to recurrence of the disease or death."),
    "onco:endpoint.dss": ("Disease-specific survival", "Time to death from the disease."),
    "onco:origin.diagnosis": ("Diagnosis", "Time zero is the date of diagnosis."),
    "onco:origin.specimen_collection": (
        "Specimen collection",
        "Time zero is the date the specimen was collected.",
    ),
    "onco:origin.treatment_start": ("Treatment start", "Time zero is the date treatment started."),
    "onco:origin.first_sequencing": (
        "First sequencing",
        "Time zero is the date of the first sequencing.",
    ),
}
"""Each concept's ``(label, definition)``, which ``resources/read`` serves."""

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
    ("Frame_Shift_Del", "Frameshift deletion"),
    ("Frame_Shift_Ins", "Frameshift insertion"),
    ("In_Frame_Del", "In-frame deletion"),
    ("In_Frame_Ins", "In-frame insertion"),
    ("Missense_Mutation", "Missense"),
    ("Nonsense_Mutation", "Nonsense"),
    ("Splice_Site", "Splice site"),
    ("Translation_Start_Site", "Start lost"),
    ("Nonstop_Mutation", "Nonstop"),
    ("Targeted_Region", "Targeted region"),
    ("De_novo_Start_InFrame", "De novo start, in frame"),
    ("De_novo_Start_OutOfFrame", "De novo start, out of frame"),
    ("Silent", "Silent"),
    ("Intron", "Intron"),
    ("3'UTR", "3' UTR"),
    ("3'Flank", "3' flank"),
    ("5'UTR", "5' UTR"),
    ("5'Flank", "5' flank"),
    ("IGR", "Intergenic"),
    ("RNA", "Non-coding RNA"),
    ("Splice_Region", "Splice region"),
    ("Fusion", "Fusion"),
    ("Unknown", "Unknown"),
]
"""cBioPortal's ``VARIANT_CLASSIFICATION_VALUES`` (``validateData.py``), in its order, with the
labels the pack gives them."""

CITED = {
    "Frame_Shift_Del": ("SO:0001589", "frameshift_variant", "broader"),
    "Frame_Shift_Ins": ("SO:0001589", "frameshift_variant", "broader"),
    "In_Frame_Del": ("SO:0001822", "inframe_deletion", "exact"),
    "In_Frame_Ins": ("SO:0001821", "inframe_insertion", "exact"),
    "Missense_Mutation": ("SO:0001583", "missense_variant", "exact"),
    "Nonsense_Mutation": ("SO:0001587", "stop_gained", "exact"),
    "Splice_Site": ("SO:0001629", "splice_site_variant", "related"),
    "Translation_Start_Site": ("SO:0002012", "start_lost", "exact"),
    "Nonstop_Mutation": ("SO:0001578", "stop_lost", "exact"),
    "Silent": ("SO:0001819", "synonymous_variant", "exact"),
    "Intron": ("SO:0001627", "intron_variant", "broader"),
    "3'UTR": ("SO:0001624", "3_prime_UTR_variant", "exact"),
    "3'Flank": ("SO:0001632", "downstream_gene_variant", "exact"),
    "5'UTR": ("SO:0001623", "5_prime_UTR_variant", "exact"),
    "5'Flank": ("SO:0001631", "upstream_gene_variant", "exact"),
    "IGR": ("SO:0001628", "intergenic_variant", "broader"),
    "RNA": ("SO:0001619", "non_coding_transcript_variant", "broader"),
    "Splice_Region": ("SO:0001630", "splice_region_variant", "exact"),
    "Fusion": ("SO:0001565", "gene_fusion", "exact"),
}
"""The SO term each mutation class cites; the others (``Targeted_Region``, the two de novo
starts and ``Unknown``) cite none."""

SO_IS_A = {
    "SO:0001060": (),  # sequence_variant
    "SO:0001537": ("SO:0001060",),  # structural_variant
    "SO:0001564": ("SO:0001878",),  # gene_variant
    "SO:0001565": ("SO:0001564", "SO:0001882"),  # gene_fusion
    "SO:0001568": ("SO:0001576",),  # splicing_variant
    "SO:0001576": ("SO:0001564",),  # transcript_variant
    "SO:0001578": ("SO:0001590", "SO:0001907", "SO:0001992"),  # stop_lost
    "SO:0001580": ("SO:0001791", "SO:0001968"),  # coding_sequence_variant
    "SO:0001582": ("SO:0001580",),  # initiator_codon_variant
    "SO:0001583": ("SO:0001992",),  # missense_variant
    "SO:0001587": ("SO:0001906", "SO:0001992"),  # stop_gained
    "SO:0001589": ("SO:0001818",),  # frameshift_variant
    "SO:0001590": ("SO:0001580",),  # terminator_codon_variant
    "SO:0001619": ("SO:0001576",),  # non_coding_transcript_variant
    "SO:0001622": ("SO:0001791", "SO:0001968"),  # UTR_variant
    "SO:0001623": ("SO:0001622",),  # 5_prime_UTR_variant
    "SO:0001624": ("SO:0001622",),  # 3_prime_UTR_variant
    "SO:0001627": ("SO:0001576",),  # intron_variant
    "SO:0001628": ("SO:0001878",),  # intergenic_variant
    "SO:0001629": ("SO:0001568", "SO:0001627"),  # splice_site_variant
    "SO:0001630": ("SO:0001568",),  # splice_region_variant
    "SO:0001631": ("SO:0001628",),  # upstream_gene_variant
    "SO:0001632": ("SO:0001628",),  # downstream_gene_variant
    "SO:0001650": ("SO:0001818",),  # inframe_variant
    "SO:0001791": ("SO:0001576",),  # exon_variant
    "SO:0001818": ("SO:0001580",),  # protein_altering_variant
    "SO:0001819": ("SO:0001580",),  # synonymous_variant
    "SO:0001820": ("SO:0001650",),  # inframe_indel
    "SO:0001821": ("SO:0001820", "SO:0001908"),  # inframe_insertion
    "SO:0001822": ("SO:0001820", "SO:0001906"),  # inframe_deletion
    "SO:0001878": ("SO:0001537",),  # feature_variant
    "SO:0001882": ("SO:0001537",),  # feature_fusion
    "SO:0001906": ("SO:0001878",),  # feature_truncation
    "SO:0001907": ("SO:0001878",),  # feature_elongation
    "SO:0001908": ("SO:0001907",),  # internal_feature_elongation
    "SO:0001968": ("SO:0001576",),  # coding_transcript_variant
    "SO:0001992": ("SO:0001650",),  # nonsynonymous_variant
    "SO:0002012": ("SO:0001582", "SO:0001992"),  # start_lost
}
"""The ``is_a`` edges, in ``so.obo`` (data-version 2026-08-07), of each term the pack cites and
of all its ancestors: each term's parents."""

CONTRADICTED = {
    ("IGR", "3'Flank"),
    ("IGR", "5'Flank"),
    ("Intron", "Splice_Site"),
}
"""The pairs ``(A, B)`` the first draft's relations made contradict the classes' disjointness: ``A``
``exact`` for a term that ``B``'s ``exact`` or ``broader`` term is under."""

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

PROFILES = [
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
]
"""The 19 pairs whose meta type has a ``stable_id`` and a ``profile_name`` in ``META_FIELD_MAP``."""


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
    assert {id: (concept.label, concept.definition) for id, concept in concepts.items()} == TEXTS
    for concept in concepts.values():
        assert concept.fields.units is None
        if concept.id not in ("onco:sample_type", "onco:mutation_class", "onco:cna_level"):
            assert concept.fields.permissible_values is None, concept.id


def test_the_value_lists(registry: PackRegistry) -> None:
    concepts = _by_id(registry)
    assert _values(concepts["onco:sample_type"]) == (SAMPLE_TYPES, False)
    classes = _values(concepts["onco:mutation_class"])
    assert classes is not None
    assert classes[0] == MUTATION_CLASSES
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


def _ancestors(term: str) -> set[str]:
    found: set[str] = set()
    pending = [term]
    while pending:
        for parent in SO_IS_A[pending.pop()]:
            if parent not in found:
                found.add(parent)
                pending.append(parent)
    return found


def _contradictions(cited: dict[str, tuple[str, str]]) -> set[tuple[str, str]]:
    """The ordered pairs of classes ``(A, B)`` where ``A`` is ``exact`` for a term that is the
    term ``B`` cites as ``exact`` or ``broader``, or one of its ancestors. A call has one class, so
    ``B`` would then be within ``A``."""
    found: set[tuple[str, str]] = set()
    for first, (term, relation) in cited.items():
        if relation != "exact":
            continue
        for second, (other, how) in cited.items():
            if (
                second != first
                and how in ("exact", "broader")
                and (term == other or term in _ancestors(other))
            ):
                found.add((first, second))
    return found


def test_the_is_a_edges_are_closed_and_cover_each_cited_term() -> None:
    assert {code for code, _, _ in CITED.values()} <= set(SO_IS_A)
    assert {parent for parents in SO_IS_A.values() for parent in parents} <= set(SO_IS_A)
    assert all(term not in _ancestors(term) for term in SO_IS_A)


def test_no_class_is_part_of_another(registry: PackRegistry) -> None:
    """The classes are disjoint, so no class's ``exact`` term may be, or be above, another
    class's ``exact`` or ``broader`` term: what a citation's relation must never reintroduce."""
    found = {
        value.value: (term.code, term.relation)
        for concept in registry.concepts()
        for value in (
            concept.fields.permissible_values.values if concept.fields.permissible_values else ()
        )
        for term in value.concepts or ()
    }
    assert set(found) == set(CITED)
    assert _contradictions(found) == set()


def test_the_check_finds_what_the_first_draft_got_wrong() -> None:
    first = {value: (code, relation) for value, (code, _, relation) in CITED.items()}
    first["Splice_Site"] = (first["Splice_Site"][0], "broader")
    for value in ("Intron", "IGR"):
        first[value] = (first[value][0], "exact")
    assert _contradictions(first) == CONTRADICTED
    assert _contradictions({"A": ("SO:0001627", "exact"), "B": ("SO:0001627", "exact")}) == {
        ("A", "B"),
        ("B", "A"),
    }
    assert (
        _contradictions({"A": ("SO:0001627", "broader"), "B": ("SO:0001629", "broader")}) == set()
    )


def test_the_ontology_systems(onco: ModuleType) -> None:
    assert sorted(onco.PACK.ontology_systems) == SYSTEMS


def test_the_extension_schemas(registry: PackRegistry, onco: ModuleType) -> None:
    pairs: tuple[str, ...] = onco.schemas.PAIRS
    assert pairs == tuple(PAIRS)
    assert len(set(PAIRS)) == 28
    assert tuple(PROFILES) == onco.schemas.PROFILES
    assert len(PROFILES) == 19
    assert set(PROFILES) <= set(PAIRS)
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

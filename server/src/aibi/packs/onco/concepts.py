"""The oncology pack's concepts (SPEC §5.7): ``onco:``, each at version 1.

Their ids are a stable contract (D391): a concept is added, never renamed or removed, and a
concept's ``version`` is bumped when its fields change, each with a pack version bump.

- **Tables:** ``onco:patient``, ``onco:sample``, ``onco:gene``.
- **Values:**
  - ``onco:gene_symbol``, with neither units nor permissible values;
  - ``onco:sample_type``, cBioPortal's seven ``Sample.Type`` constants, labelled with its display
    strings. They cite no NCIt term: NCIt could not be reached to verify a code;
  - ``onco:mutation_class``, the 23 ``Variant_Classification`` values cBioPortal's validator
    accepts, each citing a Sequence Ontology term where one was verified (SO release 2026-08-07);
  - ``onco:cna_level``, the five discrete copy-number levels, ordered.
- **Endpoints:** overall, progression-free, disease-free and disease-specific survival.
- **Time origins:** diagnosis, specimen collection, treatment start and first sequencing.

A reference's ``relation`` says how the cited term stands to the value: ``broader`` means the
cited term is broader than the value.
"""

from typing import Literal

from aibi.core.schema.descriptors import (
    ConceptDescriptor,
    ConceptFields,
    OntologyRef,
    PermissibleValue,
    PermissibleValues,
)

Relation = Literal["exact", "broader", "narrower", "related"]
Sort = Literal["value", "table", "endpoint", "time_origin"]


def _concept(
    id: str,
    label: str,
    definition: str,
    sort: Sort,
    values: PermissibleValues | None = None,
) -> ConceptDescriptor:
    fields = (
        ConceptFields(sort=sort)
        if values is None
        else ConceptFields(sort=sort, permissible_values=values)
    )
    return ConceptDescriptor(
        kind="concept", id=id, version=1, label=label, definition=definition, fields=fields
    )


def _so(code: str, name: str, relation: Relation = "exact") -> OntologyRef:
    return OntologyRef(system="SO", code=code, label=name, relation=relation)


def _value(value: str, label: str, cited: OntologyRef | None = None) -> PermissibleValue:
    if cited is None:
        return PermissibleValue(value=value, label=label)
    return PermissibleValue(value=value, label=label, concepts=[cited])


SAMPLE_TYPES = PermissibleValues(
    values=[
        _value("PRIMARY_SOLID_TUMOR", "Primary Solid Tumor"),
        _value("RECURRENT_SOLID_TUMOR", "Recurrent Solid Tumor"),
        _value("PRIMARY_BLOOD_TUMOR", "Primary Blood Tumor"),
        _value("RECURRENT_BLOOD_TUMOR", "Recurrent Blood Tumor"),
        _value("METASTATIC", "Metastatic"),
        _value("BLOOD_DERIVED_NORMAL", "Blood Derived Normal"),
        _value("SOLID_TISSUES_NORMAL", "Solid Tissues Normal"),
    ]
)
"""cBioPortal's ``Sample.Type``: its constants as values, its display strings as labels."""

MUTATION_CLASSES = PermissibleValues(
    values=[
        _value(
            "Frame_Shift_Del",
            "Frameshift deletion",
            _so("SO:0001589", "frameshift_variant", "broader"),
        ),
        _value(
            "Frame_Shift_Ins",
            "Frameshift insertion",
            _so("SO:0001589", "frameshift_variant", "broader"),
        ),
        _value("In_Frame_Del", "In-frame deletion", _so("SO:0001822", "inframe_deletion")),
        _value("In_Frame_Ins", "In-frame insertion", _so("SO:0001821", "inframe_insertion")),
        _value("Missense_Mutation", "Missense", _so("SO:0001583", "missense_variant")),
        _value("Nonsense_Mutation", "Nonsense", _so("SO:0001587", "stop_gained")),
        _value(
            "Splice_Site",
            "Splice site",
            _so("SO:0001629", "splice_site_variant", "broader"),
        ),
        _value("Translation_Start_Site", "Start lost", _so("SO:0002012", "start_lost")),
        _value("Nonstop_Mutation", "Nonstop", _so("SO:0001578", "stop_lost")),
        _value("Targeted_Region", "Targeted region"),
        _value("De_novo_Start_InFrame", "De novo start, in frame"),
        _value("De_novo_Start_OutOfFrame", "De novo start, out of frame"),
        _value("Silent", "Silent", _so("SO:0001819", "synonymous_variant")),
        _value("Intron", "Intron", _so("SO:0001627", "intron_variant")),
        _value("3'UTR", "3' UTR", _so("SO:0001624", "3_prime_UTR_variant")),
        _value("3'Flank", "3' flank", _so("SO:0001632", "downstream_gene_variant")),
        _value("5'UTR", "5' UTR", _so("SO:0001623", "5_prime_UTR_variant")),
        _value("5'Flank", "5' flank", _so("SO:0001631", "upstream_gene_variant")),
        _value("IGR", "Intergenic", _so("SO:0001628", "intergenic_variant")),
        _value(
            "RNA",
            "Non-coding RNA",
            _so("SO:0001619", "non_coding_transcript_variant", "broader"),
        ),
        _value("Splice_Region", "Splice region", _so("SO:0001630", "splice_region_variant")),
        _value("Fusion", "Fusion", _so("SO:0001565", "gene_fusion")),
        _value("Unknown", "Unknown"),
    ]
)
"""cBioPortal's ``VARIANT_CLASSIFICATION_VALUES``: the MAF specification's twelve classes, the
eight it skips by default, ``Splice_Region``, ``Fusion`` and ``Unknown``."""

CNA_LEVELS = PermissibleValues(
    values=[
        _value("-2", "Deep deletion"),
        _value("-1", "Shallow deletion"),
        _value("0", "Diploid"),
        _value("1", "Gain"),
        _value("2", "Amplification"),
    ],
    ordered=True,
)
"""The discrete copy-number levels, in order."""

CONCEPTS: tuple[ConceptDescriptor, ...] = (
    _concept("onco:patient", "Patient", "Rows are patients: the people a study follows.", "table"),
    _concept(
        "onco:sample",
        "Sample",
        "Rows are samples: specimens taken from patients and profiled.",
        "table",
    ),
    _concept("onco:gene", "Gene", "Rows are genes.", "table"),
    _concept("onco:gene_symbol", "Gene symbol", "A gene's approved symbol.", "value"),
    _concept(
        "onco:sample_type",
        "Sample type",
        "What a sample is: a primary, recurrent or metastatic tumour, or normal tissue or blood.",
        "value",
        SAMPLE_TYPES,
    ),
    _concept(
        "onco:mutation_class",
        "Mutation class",
        "The class of a mutation call, as a MAF file's Variant_Classification gives it.",
        "value",
        MUTATION_CLASSES,
    ),
    _concept(
        "onco:cna_level",
        "Copy-number level",
        "A discrete copy-number call, from deep deletion to amplification.",
        "value",
        CNA_LEVELS,
    ),
    _concept(
        "onco:endpoint.os",
        "Overall survival",
        "Time to death from any cause.",
        "endpoint",
    ),
    _concept(
        "onco:endpoint.pfs",
        "Progression-free survival",
        "Time to disease progression or death.",
        "endpoint",
    ),
    _concept(
        "onco:endpoint.dfs",
        "Disease-free survival",
        "Time to recurrence of the disease or death.",
        "endpoint",
    ),
    _concept(
        "onco:endpoint.dss",
        "Disease-specific survival",
        "Time to death from the disease.",
        "endpoint",
    ),
    _concept(
        "onco:origin.diagnosis",
        "Diagnosis",
        "Time zero is the date of diagnosis.",
        "time_origin",
    ),
    _concept(
        "onco:origin.specimen_collection",
        "Specimen collection",
        "Time zero is the date the specimen was collected.",
        "time_origin",
    ),
    _concept(
        "onco:origin.treatment_start",
        "Treatment start",
        "Time zero is the date treatment started.",
        "time_origin",
    ),
    _concept(
        "onco:origin.first_sequencing",
        "First sequencing",
        "Time zero is the date of the first sequencing.",
        "time_origin",
    ),
)
"""The pack's 15 concepts."""

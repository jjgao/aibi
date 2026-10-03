"""The pack's contract, pinned (D391): its manifest, its 15 concepts with their sorts, versions
and value lists, the Sequence Ontology terms its values cite, cBioPortal's 28 ``meta`` pairs and
its extension schemas' members. Concept ids are added, never renamed or removed, and a change to
any of this is made here on purpose, with a pack version bump; a golden that fails is the point.

The citations were checked against the Sequence Ontology's ``so.obo`` (data-version 2026-08-07):
each code names the term given and none is obsolete. No mutation class cites ``exact`` or
``narrower`` (a class is the most severe of a call's consequences, not the set of a term's calls),
and each other relation is held to what vcf2maf files in each class (``_violations``): a ``broader``
citation holds every term vcf2maf puts in the class (``VCF2MAF``, with ``FALLBACKS`` for its generic
calls) at or under the cited term (``DOWN``, and the ``SO_IS_A`` edges it is held to), and a
``related`` one is shown not broader by a term vcf2maf puts in the class outside the cited one. That
is a check of what is written down here, no more complete than those lists: a fact about a class
that none of them states is not found. ``derive_contract.py``, which is not run in CI, derives
``SO_IS_A``, ``DOWN`` and ``VCF2MAF`` again from ``so.obo`` and ``vcf2maf.pl``. NCIt could not be
reached, so no value cites it.
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
    "In_Frame_Del": ("SO:0001822", "inframe_deletion", "broader"),
    "In_Frame_Ins": ("SO:0001821", "inframe_insertion", "broader"),
    "Missense_Mutation": ("SO:0001583", "missense_variant", "broader"),
    "Nonsense_Mutation": ("SO:0001587", "stop_gained", "broader"),
    "Splice_Site": ("SO:0001629", "splice_site_variant", "related"),
    "Translation_Start_Site": ("SO:0001582", "initiator_codon_variant", "broader"),
    "Nonstop_Mutation": ("SO:0001578", "stop_lost", "broader"),
    "Silent": ("SO:0001819", "synonymous_variant", "broader"),
    "Intron": ("SO:0001627", "intron_variant", "broader"),
    "3'UTR": ("SO:0001624", "3_prime_UTR_variant", "broader"),
    "3'Flank": ("SO:0001632", "downstream_gene_variant", "broader"),
    "5'UTR": ("SO:0001623", "5_prime_UTR_variant", "broader"),
    "5'Flank": ("SO:0001631", "upstream_gene_variant", "broader"),
    "IGR": ("SO:0001628", "intergenic_variant", "broader"),
    "RNA": ("SO:0001619", "non_coding_transcript_variant", "broader"),
    "Splice_Region": ("SO:0001630", "splice_region_variant", "related"),
}
"""The SO term each mutation class cites, 18 of them; the others (``Targeted_Region``, the two de
novo starts, ``Fusion`` and ``Unknown``) cite none."""

SO_IS_A = {
    "SO:0000001": ("SO:0000110",),  # region
    "SO:0000110": (),  # sequence_feature
    "SO:0000605": ("SO:0001411",),  # intergenic_region
    "SO:0000831": ("SO:0001411",),  # gene_member_region
    "SO:0001060": (),  # sequence_variant
    "SO:0001411": ("SO:0000001",),  # biological_region
    "SO:0001536": ("SO:0001060",),  # functional_effect_variant
    "SO:0001537": ("SO:0001060",),  # structural_variant
    "SO:0001564": ("SO:0001878",),  # gene_variant
    "SO:0001566": ("SO:0001878",),  # regulatory_region_variant
    "SO:0001567": ("SO:0001590", "SO:0001819"),  # stop_retained_variant
    "SO:0001568": ("SO:0001576",),  # splicing_variant
    "SO:0001572": ("SO:0001568",),  # exon_loss_variant
    "SO:0001574": ("SO:0001629",),  # splice_acceptor_variant
    "SO:0001575": ("SO:0001629",),  # splice_donor_variant
    "SO:0001576": ("SO:0001564",),  # transcript_variant
    "SO:0001578": ("SO:0001590", "SO:0001907", "SO:0001992"),  # stop_lost
    "SO:0001580": ("SO:0001791", "SO:0001968"),  # coding_sequence_variant
    "SO:0001582": ("SO:0001580",),  # initiator_codon_variant
    "SO:0001583": ("SO:0001992",),  # missense_variant
    "SO:0001585": ("SO:0001583",),  # conservative_missense_variant
    "SO:0001586": ("SO:0001583",),  # non_conservative_missense_variant
    "SO:0001587": ("SO:0001906", "SO:0001992"),  # stop_gained
    "SO:0001589": ("SO:0001818",),  # frameshift_variant
    "SO:0001590": ("SO:0001580",),  # terminator_codon_variant
    "SO:0001591": ("SO:0001589",),  # frame_restoring_variant
    "SO:0001592": ("SO:0001589",),  # minus_1_frameshift_variant
    "SO:0001593": ("SO:0001589",),  # minus_2_frameshift_variant
    "SO:0001594": ("SO:0001589",),  # plus_1_frameshift_variant
    "SO:0001595": ("SO:0001589",),  # plus_2_frameshift_variant
    "SO:0001619": ("SO:0001576",),  # non_coding_transcript_variant
    "SO:0001620": ("SO:0001619",),  # mature_miRNA_variant
    "SO:0001621": ("SO:0001576",),  # NMD_transcript_variant
    "SO:0001622": ("SO:0001791", "SO:0001968"),  # UTR_variant
    "SO:0001623": ("SO:0001622",),  # 5_prime_UTR_variant
    "SO:0001624": ("SO:0001622",),  # 3_prime_UTR_variant
    "SO:0001626": ("SO:0001590", "SO:0001650"),  # incomplete_terminal_codon_variant
    "SO:0001627": ("SO:0001576",),  # intron_variant
    "SO:0001628": ("SO:0001878",),  # intergenic_variant
    "SO:0001629": ("SO:0001568", "SO:0001627"),  # splice_site_variant
    "SO:0001630": ("SO:0001568",),  # splice_region_variant
    "SO:0001631": ("SO:0001628",),  # upstream_gene_variant
    "SO:0001632": ("SO:0001628",),  # downstream_gene_variant
    "SO:0001633": ("SO:0001632",),  # 5KB_downstream_variant
    "SO:0001634": ("SO:0001632",),  # 500B_downstream_variant
    "SO:0001635": ("SO:0001631",),  # 5KB_upstream_variant
    "SO:0001636": ("SO:0001631",),  # 2KB_upstream_variant
    "SO:0001650": ("SO:0001818",),  # inframe_variant
    "SO:0001782": ("SO:0001566",),  # TF_binding_site_variant
    "SO:0001787": ("SO:0001629",),  # splice_donor_5th_base_variant
    "SO:0001791": ("SO:0001576",),  # exon_variant
    "SO:0001792": ("SO:0001619", "SO:0001791"),  # non_coding_transcript_exon_variant
    "SO:0001818": ("SO:0001580",),  # protein_altering_variant
    "SO:0001819": ("SO:0001580",),  # synonymous_variant
    "SO:0001820": ("SO:0001650",),  # inframe_indel
    "SO:0001821": ("SO:0001820", "SO:0001908"),  # inframe_insertion
    "SO:0001822": ("SO:0001820", "SO:0001906"),  # inframe_deletion
    "SO:0001823": ("SO:0001821",),  # conservative_inframe_insertion
    "SO:0001824": ("SO:0001821",),  # disruptive_inframe_insertion
    "SO:0001825": ("SO:0001822",),  # conservative_inframe_deletion
    "SO:0001826": ("SO:0001822",),  # disruptive_inframe_deletion
    "SO:0001878": ("SO:0001537",),  # feature_variant
    "SO:0001879": ("SO:0001537",),  # feature_ablation
    "SO:0001880": ("SO:0001537",),  # feature_amplification
    "SO:0001889": ("SO:0001880",),  # transcript_amplification
    "SO:0001893": ("SO:0001879",),  # transcript_ablation
    "SO:0001906": ("SO:0001878",),  # feature_truncation
    "SO:0001907": ("SO:0001878",),  # feature_elongation
    "SO:0001908": ("SO:0001907",),  # internal_feature_elongation
    "SO:0001909": ("SO:0001589", "SO:0001908"),  # frameshift_elongation
    "SO:0001910": ("SO:0001589", "SO:0001906"),  # frameshift_truncation
    "SO:0001968": ("SO:0001576",),  # coding_transcript_variant
    "SO:0001969": ("SO:0001627", "SO:0001968"),  # coding_transcript_intron_variant
    "SO:0001970": ("SO:0001619", "SO:0001627"),  # non_coding_transcript_intron_variant
    "SO:0001983": ("SO:0001623",),  # 5_prime_UTR_premature_start_codon_variant
    "SO:0001986": ("SO:0001628",),  # upstream_transcript_variant
    "SO:0001987": ("SO:0001628",),  # downstream_transcript_variant
    "SO:0001988": ("SO:0001983",),  # 5_prime_UTR_premature_start_codon_gain_variant
    "SO:0001989": ("SO:0001983",),  # 5_prime_UTR_premature_start_codon_loss_variant
    "SO:0001990": ("SO:0001983",),  # five_prime_UTR_premature_start_codon_location_variant
    "SO:0001992": ("SO:0001650",),  # nonsynonymous_variant
    "SO:0002008": ("SO:0001586",),  # rare_amino_acid_variant
    "SO:0002009": ("SO:0002008",),  # selenocysteine_loss
    "SO:0002010": ("SO:0002008",),  # pyrrolysine_loss
    "SO:0002011": ("SO:0001576",),  # intragenic_variant
    "SO:0002012": ("SO:0001582", "SO:0001992"),  # start_lost
    "SO:0002013": ("SO:0001623",),  # 5_prime_UTR_truncation
    "SO:0002014": ("SO:0001623",),  # 5_prime_UTR_elongation
    "SO:0002015": ("SO:0001624",),  # 3_prime_UTR_truncation
    "SO:0002016": ("SO:0001624",),  # 3_prime_UTR_elongation
    "SO:0002017": ("SO:0001628",),  # conserved_intergenic_variant
    "SO:0002018": ("SO:0001627",),  # conserved_intron_variant
    "SO:0002019": ("SO:0001582", "SO:0001819"),  # start_retained_variant
    "SO:0002074": ("SO:0001628",),  # intergenic_1kb_variant
    "SO:0002083": ("SO:0001632",),  # 2KB_downstream_variant
    "SO:0002084": ("SO:0001630",),  # exonic_splice_region_variant
    "SO:0002088": ("SO:0001619", "SO:0001630"),  # non_coding_transcript_splice_region_variant
    "SO:0002089": ("SO:0001624",),  # 3_prime_UTR_exon_variant
    "SO:0002090": ("SO:0001624", "SO:0001969"),  # 3_prime_UTR_intron_variant
    "SO:0002091": ("SO:0001623", "SO:0001969"),  # 5_prime_UTR_intron_variant
    "SO:0002092": ("SO:0001623",),  # 5_prime_UTR_exon_variant
    "SO:0002169": ("SO:0001568",),  # splice_polypyrimidine_tract_variant
    "SO:0002170": ("SO:0001630",),  # splice_donor_region_variant
    "SO:0002218": ("SO:0001536",),  # functionally_abnormal
    "SO:0002319": ("SO:0002218",),  # NMD_triggering_variant
    "SO:0002320": ("SO:0002218",),  # NMD_escaping_variant
    "SO:0002321": ("SO:0001587", "SO:0002319"),  # stop_gained_NMD_triggering
    "SO:0002322": ("SO:0001587", "SO:0002320"),  # stop_gained_NMD_escaping
    "SO:0002323": ("SO:0001589", "SO:0002319"),  # frameshift_variant_NMD_triggering
    "SO:0002324": ("SO:0001589", "SO:0002320"),  # frameshift_variant_NMD_escaping
    "SO:0002325": ("SO:0001575", "SO:0002319"),  # splice_donor_variant_NMD_triggering
    "SO:0002326": ("SO:0001575", "SO:0002320"),  # splice_donor_variant_NMD_escaping
    "SO:0002327": ("SO:0001574", "SO:0002319"),  # splice_acceptor_variant_NMD_triggering
    "SO:0002328": ("SO:0001574", "SO:0002320"),  # splice_acceptor_variant_NMD_escaping
    "SO:0002385": ("SO:0001623",),  # 5_prime_UTR_uORF_variant
    "SO:0002386": ("SO:0002385",),  # 5_prime_UTR_uORF_stop_codon_variant
    "SO:0002387": ("SO:0002385",),  # 5_prime_UTR_uORF_frameshift_variant
    "SO:0002388": ("SO:0002386",),  # 5_prime_UTR_uORF_stop_codon_gain_variant
    "SO:0002389": ("SO:0002386",),  # 5_prime_UTR_uORF_stop_codon_loss_variant
    "SO:0002398": ("SO:0002008",),  # selenocysteine_gain
    "SO:0002401": ("SO:0001628",),  # upstream_intergenic_fusion
    "SO:0002402": ("SO:0001628",),  # downstream_intergenic_fusion
    "SO:0005836": ("SO:0000831",),  # regulatory_region
}
"""The ``is_a`` edges, in ``so.obo`` (data-version 2026-08-07), of each term below and of all its
ancestors: each term's parents. The terms are the ones the pack cites, their descendants (``DOWN``),
and the ones a class holds (``VCF2MAF``)."""

DOWN = {
    "SO:0001589": (  # frameshift_variant
        "SO:0001591",  # frame_restoring_variant
        "SO:0001592",  # minus_1_frameshift_variant
        "SO:0001593",  # minus_2_frameshift_variant
        "SO:0001594",  # plus_1_frameshift_variant
        "SO:0001595",  # plus_2_frameshift_variant
        "SO:0001909",  # frameshift_elongation
        "SO:0001910",  # frameshift_truncation
        "SO:0002323",  # frameshift_variant_NMD_triggering
        "SO:0002324",  # frameshift_variant_NMD_escaping
    ),
    "SO:0001822": (  # inframe_deletion
        "SO:0001825",  # conservative_inframe_deletion
        "SO:0001826",  # disruptive_inframe_deletion
    ),
    "SO:0001821": (  # inframe_insertion
        "SO:0001823",  # conservative_inframe_insertion
        "SO:0001824",  # disruptive_inframe_insertion
    ),
    "SO:0001583": (  # missense_variant
        "SO:0001585",  # conservative_missense_variant
        "SO:0001586",  # non_conservative_missense_variant
        "SO:0002008",  # rare_amino_acid_variant
        "SO:0002009",  # selenocysteine_loss
        "SO:0002010",  # pyrrolysine_loss
        "SO:0002398",  # selenocysteine_gain
    ),
    "SO:0001587": (  # stop_gained
        "SO:0002321",  # stop_gained_NMD_triggering
        "SO:0002322",  # stop_gained_NMD_escaping
    ),
    "SO:0001629": (  # splice_site_variant
        "SO:0001574",  # splice_acceptor_variant
        "SO:0001575",  # splice_donor_variant
        "SO:0001787",  # splice_donor_5th_base_variant
        "SO:0002325",  # splice_donor_variant_NMD_triggering
        "SO:0002326",  # splice_donor_variant_NMD_escaping
        "SO:0002327",  # splice_acceptor_variant_NMD_triggering
        "SO:0002328",  # splice_acceptor_variant_NMD_escaping
    ),
    "SO:0001582": (  # initiator_codon_variant
        "SO:0002012",  # start_lost
        "SO:0002019",  # start_retained_variant
    ),
    "SO:0001578": (),  # stop_lost
    "SO:0001819": (  # synonymous_variant
        "SO:0001567",  # stop_retained_variant
        "SO:0002019",  # start_retained_variant
    ),
    "SO:0001627": (  # intron_variant
        "SO:0001574",  # splice_acceptor_variant
        "SO:0001575",  # splice_donor_variant
        "SO:0001629",  # splice_site_variant
        "SO:0001787",  # splice_donor_5th_base_variant
        "SO:0001969",  # coding_transcript_intron_variant
        "SO:0001970",  # non_coding_transcript_intron_variant
        "SO:0002018",  # conserved_intron_variant
        "SO:0002090",  # 3_prime_UTR_intron_variant
        "SO:0002091",  # 5_prime_UTR_intron_variant
        "SO:0002325",  # splice_donor_variant_NMD_triggering
        "SO:0002326",  # splice_donor_variant_NMD_escaping
        "SO:0002327",  # splice_acceptor_variant_NMD_triggering
        "SO:0002328",  # splice_acceptor_variant_NMD_escaping
    ),
    "SO:0001624": (  # 3_prime_UTR_variant
        "SO:0002015",  # 3_prime_UTR_truncation
        "SO:0002016",  # 3_prime_UTR_elongation
        "SO:0002089",  # 3_prime_UTR_exon_variant
        "SO:0002090",  # 3_prime_UTR_intron_variant
    ),
    "SO:0001632": (  # downstream_gene_variant
        "SO:0001633",  # 5KB_downstream_variant
        "SO:0001634",  # 500B_downstream_variant
        "SO:0002083",  # 2KB_downstream_variant
    ),
    "SO:0001623": (  # 5_prime_UTR_variant
        "SO:0001983",  # 5_prime_UTR_premature_start_codon_variant
        "SO:0001988",  # 5_prime_UTR_premature_start_codon_gain_variant
        "SO:0001989",  # 5_prime_UTR_premature_start_codon_loss_variant
        "SO:0001990",  # five_prime_UTR_premature_start_codon_location_variant
        "SO:0002013",  # 5_prime_UTR_truncation
        "SO:0002014",  # 5_prime_UTR_elongation
        "SO:0002091",  # 5_prime_UTR_intron_variant
        "SO:0002092",  # 5_prime_UTR_exon_variant
        "SO:0002385",  # 5_prime_UTR_uORF_variant
        "SO:0002386",  # 5_prime_UTR_uORF_stop_codon_variant
        "SO:0002387",  # 5_prime_UTR_uORF_frameshift_variant
        "SO:0002388",  # 5_prime_UTR_uORF_stop_codon_gain_variant
        "SO:0002389",  # 5_prime_UTR_uORF_stop_codon_loss_variant
    ),
    "SO:0001631": (  # upstream_gene_variant
        "SO:0001635",  # 5KB_upstream_variant
        "SO:0001636",  # 2KB_upstream_variant
    ),
    "SO:0001628": (  # intergenic_variant
        "SO:0001631",  # upstream_gene_variant
        "SO:0001632",  # downstream_gene_variant
        "SO:0001633",  # 5KB_downstream_variant
        "SO:0001634",  # 500B_downstream_variant
        "SO:0001635",  # 5KB_upstream_variant
        "SO:0001636",  # 2KB_upstream_variant
        "SO:0001986",  # upstream_transcript_variant
        "SO:0001987",  # downstream_transcript_variant
        "SO:0002017",  # conserved_intergenic_variant
        "SO:0002074",  # intergenic_1kb_variant
        "SO:0002083",  # 2KB_downstream_variant
        "SO:0002401",  # upstream_intergenic_fusion
        "SO:0002402",  # downstream_intergenic_fusion
    ),
    "SO:0001619": (  # non_coding_transcript_variant
        "SO:0001620",  # mature_miRNA_variant
        "SO:0001792",  # non_coding_transcript_exon_variant
        "SO:0001970",  # non_coding_transcript_intron_variant
        "SO:0002088",  # non_coding_transcript_splice_region_variant
    ),
    "SO:0001630": (  # splice_region_variant
        "SO:0002084",  # exonic_splice_region_variant
        "SO:0002088",  # non_coding_transcript_splice_region_variant
        "SO:0002170",  # splice_donor_region_variant
    ),
}
"""Each cited term's descendants, all the way down, in ``so.obo`` (data-version 2026-08-07): a term
is under another if it is in the other's tuple. ``SO_IS_A`` and ``DOWN`` are two readings of the
same graph, and ``test_the_descendants_and_the_is_a_edges_agree`` holds them to each other."""

VCF2MAF = {
    "Splice_Site": (
        "SO:0001574",  # splice_acceptor_variant
        "SO:0001575",  # splice_donor_variant
        "SO:0001893",  # transcript_ablation
        "SO:0001572",  # exon_loss_variant
    ),
    "Nonsense_Mutation": (
        "SO:0001587",  # stop_gained
    ),
    "Frame_Shift_Del": (
        "SO:0001589",  # frameshift_variant
        "SO:0001818",  # protein_altering_variant
    ),
    "Frame_Shift_Ins": (
        "SO:0001589",  # frameshift_variant
        "SO:0001818",  # protein_altering_variant
    ),
    "Nonstop_Mutation": (
        "SO:0001578",  # stop_lost
    ),
    "Translation_Start_Site": (
        "SO:0001582",  # initiator_codon_variant
        "SO:0002012",  # start_lost
    ),
    "In_Frame_Ins": (
        "SO:0001821",  # inframe_insertion
        "SO:0001824",  # disruptive_inframe_insertion
        "SO:0001823",  # conservative_inframe_insertion
        "SO:0001818",  # protein_altering_variant
    ),
    "In_Frame_Del": (
        "SO:0001822",  # inframe_deletion
        "SO:0001826",  # disruptive_inframe_deletion
        "SO:0001825",  # conservative_inframe_deletion
        "SO:0001818",  # protein_altering_variant
    ),
    "Missense_Mutation": (
        "SO:0001583",  # missense_variant
        "SO:0001580",  # coding_sequence_variant
        "SO:0001585",  # conservative_missense_variant
        "SO:0002008",  # rare_amino_acid_variant
    ),
    "Intron": (
        "SO:0001889",  # transcript_amplification
        "SO:0001627",  # intron_variant
        "SO:0002011",  # intragenic_variant
    ),
    "Splice_Region": (
        "SO:0001630",  # splice_region_variant
        "SO:0001787",  # splice_donor_5th_base_variant
        "SO:0002170",  # splice_donor_region_variant
        "SO:0002169",  # splice_polypyrimidine_tract_variant
    ),
    "Silent": (
        "SO:0001626",  # incomplete_terminal_codon_variant
        "SO:0001819",  # synonymous_variant
        "SO:0001567",  # stop_retained_variant
        "SO:0002019",  # start_retained_variant
        "SO:0001621",  # NMD_transcript_variant
    ),
    "RNA": (
        "SO:0001620",  # mature_miRNA_variant
        "SO:0001791",  # exon_variant
        "SO:0001792",  # non_coding_transcript_exon_variant
        "SO:0001619",  # non_coding_transcript_variant
    ),
    "5'UTR": (
        "SO:0001623",  # 5_prime_UTR_variant
        "SO:0001988",  # 5_prime_UTR_premature_start_codon_gain_variant
    ),
    "3'UTR": (
        "SO:0001624",  # 3_prime_UTR_variant
    ),
    "IGR": (
        "SO:0001782",  # TF_binding_site_variant
        "SO:0001566",  # regulatory_region_variant
        "SO:0005836",  # regulatory_region
        "SO:0001628",  # intergenic_variant
        "SO:0000605",  # intergenic_region
    ),
    "5'Flank": (
        "SO:0001631",  # upstream_gene_variant
    ),
    "3'Flank": (
        "SO:0001632",  # downstream_gene_variant
    ),
}
"""Each class's Sequence Ontology terms in ``vcf2maf.pl``'s ``GetVariantClassification`` (lines
1051 to 1068), by SO id and in its order. The names it uses that ``so.obo`` does not have
(``INTRAGENIC``, ``non_coding_exon_variant``, ``nc_transcript_variant``) are left out. A class
vcf2maf maps nothing to (``Targeted_Region`` is what is left over, and ``Unknown``, the two de novo
starts and ``Fusion`` have no row) holds nothing here."""

FALLBACKS = {
    ("Frame_Shift_Del", "SO:0001818"): "vcf2maf's protein_altering_variant that is not in frame",
    ("Frame_Shift_Ins", "SO:0001818"): "vcf2maf's protein_altering_variant that is not in frame",
    ("In_Frame_Del", "SO:0001818"): "vcf2maf's protein_altering_variant that is in frame",
    ("In_Frame_Ins", "SO:0001818"): "vcf2maf's protein_altering_variant that is in frame",
    ("Missense_Mutation", "SO:0001580"): "VEP's generic coding_sequence_variant, taken as missense",
    ("Silent", "SO:0001626"): "incomplete_terminal_codon_variant, taken as silent",
    ("Silent", "SO:0001621"): "NMD_transcript_variant, taken as silent",
    ("Intron", "SO:0001889"): "transcript_amplification, which vcf2maf files as Intron",
    (
        "Intron",
        "SO:0002011",
    ): "intragenic_variant, a variant of a gene and no more, taken as Intron",
    ("IGR", "SO:0001782"): "TF_binding_site_variant, which vcf2maf files as IGR",
    ("IGR", "SO:0001566"): "regulatory_region_variant, which vcf2maf files as IGR",
    ("IGR", "SO:0005836"): "regulatory_region, which vcf2maf files as IGR",
    ("IGR", "SO:0000605"): "intergenic_region, which vcf2maf files as IGR",
    ("RNA", "SO:0001791"): "VEP's generic exon_variant, taken as RNA",
}
"""The calls vcf2maf files in a class although the class's ``broader`` term does not hold them: its
generic fallbacks, each with its reason, by ``(class, term)``. A class holds these and its relation
is still the one judged by the rest of its calls; a pair that is not needed, or names a term the
class does not hold, fails ``test_the_fallbacks_are_each_needed``. One exception is not here, since
it is no term: an empty ``Consequence`` is tagged ``intergenic_variant`` (``vcf2maf.pl`` line 813),
so ``IGR`` holds a call that carries no term at all."""

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


def _ancestors(is_a: dict[str, tuple[str, ...]], term: str) -> set[str]:
    found: set[str] = set()
    pending = [term]
    while pending:
        for parent in is_a[pending.pop()]:
            if parent not in found:
                found.add(parent)
                pending.append(parent)
    return found


def _closure_mismatches(
    is_a: dict[str, tuple[str, ...]], down: dict[str, tuple[str, ...]]
) -> set[tuple[str, str]]:
    """The pairs ``(term, x)`` where ``x`` is under ``term`` by ``is_a``'s edges or by ``down``,
    and not by the other."""
    return {
        (term, x)
        for term, below in down.items()
        for x in is_a
        if (term in _ancestors(is_a, x)) != (x in below)
    }


def _members() -> dict[str, frozenset[str]]:
    return {value: frozenset(terms) for value, terms in VCF2MAF.items()}


def _violations(
    cited: dict[str, tuple[str, str]],
    members: dict[str, frozenset[str]],
    down: dict[str, tuple[str, ...]],
    fallbacks: set[tuple[str, str]],
) -> set[tuple[str, str, str]]:
    """What a citation's relation says that what vcf2maf files in the class contradicts.

    A mutation class cites ``broader`` or ``related`` and nothing else: it is the most severe of a
    call's consequences, not the set of a term's calls, so no source says when a term is ``exact``
    or ``narrower`` for it. A relation is proved from vcf2maf's filing (``members``: the terms it
    puts in each class), and holds under the one definition the MAF specification gives. For a
    class ``A`` that cites ``T`` (``cited`` maps a class to its term and relation, ``down`` each
    term to the terms under it, ``fallbacks`` the ``(class, term)`` pairs vcf2maf's generic calls
    are let off):

    - ``("relation", A, r)``: ``r`` is neither ``broader`` nor ``related``;
    - ``("memberless", A, T)``: ``A`` is ``broader`` and vcf2maf files no term in it, so nothing
      shows it is within ``T``;
    - ``("outside", A, m)``: ``A`` is ``broader`` and holds ``m``, which is neither ``T`` nor under
      it;
    - ``("unwitnessed", A, T)``: ``A`` is ``related`` (neither the same, nor broader, nor
      narrower: SPEC §5.4) and holds no term outside ``T`` that is not a fallback, so as far as
      vcf2maf shows it might be ``broader``. A term outside ``T`` is the witness that it is not;
    - ``("disjoint", A, T)``: ``A`` is ``related`` and holds no term at or under ``T``, so it shares
      no call with ``T``: that is a wrong code, not a ``related`` one.

    The not-narrower half of ``related`` is not checked here: the term map cannot show it, and
    D393 records the witnesses.
    """
    found: set[tuple[str, str, str]] = set()
    for first, (term, relation) in cited.items():
        if relation not in ("broader", "related"):
            found.add(("relation", first, relation))
            continue
        inside = {term, *down[term]}
        outside = {
            held
            for held in members.get(first, ())
            if held not in inside and (first, held) not in fallbacks
        }
        if relation == "related":
            if not outside:
                found.add(("unwitnessed", first, term))
            if not inside & members.get(first, frozenset()):
                found.add(("disjoint", first, term))
            continue
        if not members.get(first):
            found.add(("memberless", first, term))
        found |= {("outside", first, held) for held in outside}
    return found


def _given(registry: PackRegistry) -> dict[str, tuple[str, str]]:
    return {
        value.value: (term.code, term.relation)
        for concept in registry.concepts()
        for value in (
            concept.fields.permissible_values.values if concept.fields.permissible_values else ()
        )
        for term in value.concepts or ()
    }


def test_the_descendants_and_the_is_a_edges_agree() -> None:
    codes = {code for code, _, _ in CITED.values()}
    assert set(DOWN) == codes
    assert codes <= set(SO_IS_A)
    assert {x for below in DOWN.values() for x in below} <= set(SO_IS_A)
    assert {x for terms in _members().values() for x in terms} <= set(SO_IS_A)
    assert {parent for parents in SO_IS_A.values() for parent in parents} <= set(SO_IS_A)
    assert all(term not in _ancestors(SO_IS_A, term) for term in SO_IS_A)
    assert _closure_mismatches(SO_IS_A, DOWN) == set()
    named = set(DOWN) | {x for below in DOWN.values() for x in below}
    named |= {x for terms in _members().values() for x in terms}
    assert set(SO_IS_A) == named | {above for x in named for above in _ancestors(SO_IS_A, x)}


def test_the_closures_of_a_grandparent_are_held_to_each_other() -> None:
    """``DOWN`` says what is under a term all the way down, so a grandchild is under it too."""
    chain = {"c": ("b",), "b": ("a",), "a": ()}
    assert _ancestors(chain, "c") == {"a", "b"}
    assert _closure_mismatches(chain, {"a": ("b", "c")}) == set()
    assert _closure_mismatches(chain, {"a": ("b", "c"), "b": ("c",)}) == set()
    assert _closure_mismatches(chain, {"a": ("b",)}) == {("a", "c")}
    assert _closure_mismatches(chain, {"a": ("b", "c"), "b": ()}) == {("b", "c")}
    assert _closure_mismatches(chain, {"c": ("a",)}) == {("c", "a")}


def test_what_a_class_holds_names_its_reasons() -> None:
    held = _members()
    assert set(VCF2MAF) <= set(held)
    assert all(reason for reason in FALLBACKS.values())
    assert all(term in held[value] for value, term in FALLBACKS)


def test_the_fallbacks_are_each_needed(registry: PackRegistry) -> None:
    """A pair of the allow-list is let off a hole that is there: with it removed, the class is
    ``broader`` and holds a term outside its own."""
    cited = _given(registry)
    held, fallbacks = _members(), set(FALLBACKS)
    for pair in FALLBACKS:
        assert _violations(cited, held, DOWN, fallbacks - {pair}) == {("outside", *pair)}, pair


def test_no_relation_contradicts_what_vcf2maf_files(registry: PackRegistry) -> None:
    """Every relation is ``broader`` or ``related``: ``broader`` where every term vcf2maf files in
    the class (bar its fallbacks) is at or under the cited term, ``related`` where one is outside
    it."""
    cited = _given(registry)
    assert set(cited) == set(CITED)
    assert {relation for _, relation in cited.values()} == {"broader", "related"}
    assert _violations(cited, _members(), DOWN, set(FALLBACKS)) == set()


def _with(changes: dict[str, str | tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """The pack's citations, with these classes changed: to a relation, or to a term and a
    relation."""
    cited = {value: (code, relation) for value, (code, _, relation) in CITED.items()}
    for value, change in changes.items():
        cited[value] = (cited[value][0], change) if isinstance(change, str) else change
    return cited


def test_the_check_finds_a_relation_that_what_vcf2maf_files_contradicts() -> None:
    """Each relation a review found wrong, put back with the golden's own: the check fails it."""
    held, fallbacks = _members(), set(FALLBACKS)
    cases = {
        "Silent exact (round 3)": ({"Silent": "exact"}, {("relation", "Silent", "exact")}),
        "Intron narrower (round 3)": (
            {"Intron": "narrower"},
            {("relation", "Intron", "narrower")},
        ),
        "IGR narrower (round 3)": ({"IGR": "narrower"}, {("relation", "IGR", "narrower")}),
        "Silent narrower (round 3)": (
            {"Silent": "narrower"},
            {("relation", "Silent", "narrower")},
        ),
        "Splice_Site exact": ({"Splice_Site": "exact"}, {("relation", "Splice_Site", "exact")}),
        "Splice_Site broader (round 1)": (
            {"Splice_Site": "broader"},
            {("outside", "Splice_Site", "SO:0001572"), ("outside", "Splice_Site", "SO:0001893")},
        ),
        "Splice_Region broader": (
            {"Splice_Region": "broader"},
            {
                ("outside", "Splice_Region", "SO:0001787"),
                ("outside", "Splice_Region", "SO:0002169"),
            },
        ),
        "Nonsense_Mutation related": (
            {"Nonsense_Mutation": "related"},
            {("unwitnessed", "Nonsense_Mutation", "SO:0001587")},
        ),
        "3'UTR related": ({"3'UTR": "related"}, {("unwitnessed", "3'UTR", "SO:0001624")}),
        "5'Flank related": ({"5'Flank": "related"}, {("unwitnessed", "5'Flank", "SO:0001631")}),
        "Silent related (its only outsiders are fallbacks)": (
            {"Silent": "related"},
            {("unwitnessed", "Silent", "SO:0001819")},
        ),
        "Splice_Region related, at missense_variant": (
            {"Splice_Region": ("SO:0001583", "related")},
            {("disjoint", "Splice_Region", "SO:0001583")},
        ),
        "Splice_Site related, at stop_gained": (
            {"Splice_Site": ("SO:0001587", "related")},
            {("disjoint", "Splice_Site", "SO:0001587")},
        ),
        "Nonsense_Mutation broader, at frameshift_variant": (
            {"Nonsense_Mutation": ("SO:0001589", "broader")},
            {("outside", "Nonsense_Mutation", "SO:0001587")},
        ),
        "Missense_Mutation broader, at stop_gained": (
            {"Missense_Mutation": ("SO:0001587", "broader")},
            {
                ("outside", "Missense_Mutation", "SO:0001583"),
                ("outside", "Missense_Mutation", "SO:0001585"),
                ("outside", "Missense_Mutation", "SO:0002008"),
            },
        ),
    }
    for name, (changes, expected) in cases.items():
        assert _violations(_with(changes), held, DOWN, fallbacks) == expected, name


def test_a_class_vcf2maf_files_nothing_in_cannot_be_broader() -> None:
    """``Fusion`` has no row in ``VCF2MAF`` (vcf2maf never writes it), so a ``broader`` citation of
    it has nothing to be judged by; and a ``related`` one has no witness and shares no call."""
    down = {**DOWN, "SO:0001565": ()}
    assert "Fusion" not in VCF2MAF
    expected = {
        "broader": {("memberless", "Fusion", "SO:0001565")},
        "related": {("unwitnessed", "Fusion", "SO:0001565"), ("disjoint", "Fusion", "SO:0001565")},
    }
    for relation, violations in expected.items():
        cited = _with({"Fusion": ("SO:0001565", relation)})
        found = _violations(cited, _members(), down, set(FALLBACKS))
        assert found == violations, relation


def test_the_check_reads_the_terms_below_a_term_all_the_way_down() -> None:
    """A synthetic graph, so that each rule is shown to fail on its own: ``t`` has a child ``p``
    and a grandchild ``g``, ``l`` is a leaf under ``t``, and ``w`` is above ``t``."""
    down = {"t": ("p", "g", "l"), "w": ("t", "p", "g", "l"), "l": ()}

    def violations(
        cited: dict[str, tuple[str, str]],
        members: dict[str, frozenset[str]],
        fallbacks: set[tuple[str, str]] | None = None,
    ) -> set[tuple[str, str, str]]:
        return _violations(cited, members, down, fallbacks or set())

    broader, related = {"A": ("t", "broader")}, {"A": ("t", "related")}
    assert violations(broader, {"A": frozenset({"t", "g"})}) == set()
    assert violations(broader, {"A": frozenset({"g"})}) == set()
    assert violations(broader, {"A": frozenset({"t", "x"})}) == {("outside", "A", "x")}
    assert violations(broader, {"A": frozenset({"w"})}) == {("outside", "A", "w")}
    assert violations(broader, {"A": frozenset({"t", "x"})}, {("A", "x")}) == set()
    assert violations(broader, {"A": frozenset()}) == {("memberless", "A", "t")}
    assert violations(broader, {}) == {("memberless", "A", "t")}
    assert violations(related, {"A": frozenset({"t", "x"})}) == set()
    assert violations(related, {"A": frozenset({"g", "w"})}) == set()
    assert violations(related, {"A": frozenset({"t", "g", "l"})}) == {("unwitnessed", "A", "t")}
    assert violations(related, {"A": frozenset({"x"})}) == {("disjoint", "A", "t")}
    assert violations(related, {"A": frozenset({"w"})}) == {("disjoint", "A", "t")}
    assert violations(related, {"A": frozenset()}) == {
        ("unwitnessed", "A", "t"),
        ("disjoint", "A", "t"),
    }
    assert violations(related, {"A": frozenset({"t", "x"})}, {("A", "x")}) == {
        ("unwitnessed", "A", "t")
    }
    assert violations(related, {"A": frozenset({"g", "x", "y"})}, {("A", "x")}) == set()
    for relation in ("exact", "narrower"):
        assert violations({"A": ("t", relation)}, {"A": frozenset({"t"})}) == {
            ("relation", "A", relation)
        }


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

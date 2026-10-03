"""The oncology pack's descriptor extensions (SPEC §5.10, §10.1, D247): a JSON Schema for its
extension object on datasets, tables and columns.

Each schema is of the 2020-12 dialect, declared at its root alone, with no ``$id``, no
``pattern``, ``additionalProperties: false`` and every string capped, as the core's checks of a
pack's schemas ask. Every member is optional. A member's value is what an importer or an operator
declares from the source's own metadata, never a value computed from the rows (§10.1, D271).

- ``dataset``: a cBioPortal study's ``meta_study.txt`` fields: ``cancer_study_identifier``,
  ``type_of_cancer`` and ``reference_genome``.
- ``table``: ``meta``, the ``<genetic_alteration_type>:<datatype>`` of the meta file the table
  came from, one of cBioPortal's 28 pairs (``PAIRS``); ``stable_id``; and ``case_list_category``,
  for a coverage table made from a case list (case lists have no ``genetic_alteration_type``).
- ``column``: ``attribute_priority``, the fourth header row of a clinical file, which M4.2a's
  proposer reads by name.

The pack's next importer widens these with a pack version bump.
"""

from aibi.core.schema.pack_api import JsonSchema

DIALECT = "https://json-schema.org/draft/2020-12/schema"

PAIRS: tuple[str, ...] = (
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
)
"""cBioPortal's ``(genetic_alteration_type, datatype)`` pairs, from ``cbioportal_common``'s
``alt_type_datatype_to_meta``, written ``<genetic_alteration_type>:<datatype>``."""

PROFILES: tuple[str, ...] = (
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
"""The 19 of ``PAIRS`` that are molecular profiles: those whose meta type in ``cbioportal_common``'s
``META_FIELD_MAP`` has a ``stable_id`` and a ``profile_name``. The others are the cancer-type,
clinical, timeline, segment, GISTIC, MutSig and gene-panel-matrix files, which are not profiles."""

GENOMES: tuple[str, ...] = ("hg19", "hg38", "mm10")
"""The reference genomes a cBioPortal study declares."""

DATASET: JsonSchema = {
    "$schema": DIALECT,
    "type": "object",
    "properties": {
        "cancer_study_identifier": {"type": "string", "minLength": 1, "maxLength": 200},
        "type_of_cancer": {"type": "string", "minLength": 1, "maxLength": 64},
        "reference_genome": {"enum": list(GENOMES)},
    },
    "additionalProperties": False,
}
TABLE: JsonSchema = {
    "$schema": DIALECT,
    "type": "object",
    "properties": {
        "meta": {"enum": list(PAIRS)},
        "stable_id": {"type": "string", "minLength": 1, "maxLength": 200},
        "case_list_category": {"type": "string", "minLength": 1, "maxLength": 64},
    },
    "additionalProperties": False,
}
COLUMN: JsonSchema = {
    "$schema": DIALECT,
    "type": "object",
    "properties": {
        "attribute_priority": {"type": "integer", "minimum": -1, "maximum": 2**53 - 1},
    },
    "additionalProperties": False,
}
SCHEMAS: dict[str, JsonSchema] = {"dataset": DATASET, "table": TABLE, "column": COLUMN}
"""The pack's extension schemas, by descriptor kind."""

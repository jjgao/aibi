"""A cBioPortal study's meta files (SPEC D409): which entries are meta files, how one is read, what
type it is, the keys each type must hold, and the study meta's values.

The facts here are cBioPortal's, at cbioportal-core 47890bb, re-expressed as data and cited by
line: ``V:`` is ``scripts/importer/validateData.py`` and ``C:`` is
``scripts/importer/cbioportal_common.py``. No code or message text is taken from them; the
messages are the pack's own. Where the pack is stricter than cBioPortal, the docstring says so,
and D409 lists it.

Everything here reads untrusted text, and is total: it returns, or raises ``Refused`` with one of
``refusals.CODES``. A refusal names an entry, or a key (the pack's constant), never a value.
"""

import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass

from aibi.core.schema.pack_api import Refused
from aibi.packs.onco.ontology import oncotree
from aibi.packs.onco.refusals import entry_refused
from aibi.packs.onco.text import Checked, written

# --- Which entries are meta files (V:4709-4715) --------------------------------------------------

META_NAME = re.compile(r"(\b|_)meta(\b|[_0-9])", re.IGNORECASE)
"""An entry is meta-named when this pattern is found in its name (V:4711, Python's ``re``)."""

HIDDEN = "."
"""A name that starts with it is excluded (V:4714)."""
BACKUP_SUFFIX = "~"
"""A name that ends with it is excluded (V:4715)."""
ONCOKB_BACKUP = "ONCOKB_IMPORT_BACKUP"
"""A name that starts with it is excluded (V:4713)."""


def meta_named(name: str) -> bool:
    """Whether ``name`` is meta-named (V:4711)."""
    return META_NAME.search(name) is not None


def excluded(name: str) -> str | None:
    """Why the name is excluded from the study's meta files (V:4713-4715), as a skip note says
    it, or ``None``. An excluded entry is never read as a meta file, whatever its name."""
    if name.startswith(HIDDEN):
        return "a hidden file"
    if name.endswith(BACKUP_SUFFIX) or name.startswith(ONCOKB_BACKUP):
        return "an editor or OncoKB backup"
    return None


# --- Reading a meta file (C:852-870) -------------------------------------------------------------

META_MOST = 65_536
"""The most bytes a meta file may hold. Stricter: cBioPortal reads any size (D409)."""
LINES = re.compile(r"\r\n|\n|\r")
"""The line ends Python's universal newlines read (C:852 opens the file as text)."""
BOM = "\ufeff"


def parse(raw: bytes, name: str) -> dict[str, str]:
    """The keys and values of the meta file ``name`` (C:852-870): strict UTF-8, past one leading
    BOM (cause rule 2: a second one is content); split at ``\\r\\n``, ``\\n`` and ``\\r`` alone;
    a line blank by ``str.strip()`` skipped; any other line split at its first ``:``, the key
    kept as written and the value ``str.strip()``ped; the last of a repeated key kept. A file
    that is not UTF-8, or that holds a line without ``:``, is ``META_FILE``."""
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise entry_refused("META_FILE", name, "is not UTF-8") from None
    if content.startswith(BOM):
        content = content[len(BOM) :]
    found: dict[str, str] = {}
    for line in LINES.split(content):
        if line.strip() == "":
            continue
        key, colon, value = line.partition(":")
        if not colon:
            raise entry_refused("META_FILE", name, "has a line without ':'")
        found[key] = value.strip()
    return found


# --- Types (C:644-729) ---------------------------------------------------------------------------

PAIR_TYPES: Mapping[str, str] = {
    "CANCER_TYPE:CANCER_TYPE": "CANCER_TYPE",
    "CLINICAL:PATIENT_ATTRIBUTES": "PATIENT_ATTRIBUTES",
    "CLINICAL:SAMPLE_ATTRIBUTES": "SAMPLE_ATTRIBUTES",
    "CLINICAL:TIMELINE": "TIMELINE",
    "PROTEIN_LEVEL:LOG2-VALUE": "PROTEIN",
    "PROTEIN_LEVEL:Z-SCORE": "PROTEIN",
    "PROTEIN_LEVEL:CONTINUOUS": "PROTEIN",
    "COPY_NUMBER_ALTERATION:DISCRETE": "CNA_DISCRETE",
    "COPY_NUMBER_ALTERATION:DISCRETE_LONG": "CNA_DISCRETE_LONG",
    "COPY_NUMBER_ALTERATION:CONTINUOUS": "CNA_CONTINUOUS",
    "COPY_NUMBER_ALTERATION:LOG2-VALUE": "CNA_LOG2",
    "COPY_NUMBER_ALTERATION:SEG": "SEG",
    "MRNA_EXPRESSION:CONTINUOUS": "EXPRESSION",
    "MRNA_EXPRESSION:Z-SCORE": "EXPRESSION",
    "MRNA_EXPRESSION:DISCRETE": "EXPRESSION",
    "MUTATION_EXTENDED:MAF": "MUTATION",
    "MUTATION_UNCALLED:MAF": "MUTATION_UNCALLED",
    "METHYLATION:CONTINUOUS": "METHYLATION",
    "GENE_PANEL_MATRIX:GENE_PANEL_MATRIX": "GENE_PANEL_MATRIX",
    "STRUCTURAL_VARIANT:SV": "STRUCTURAL_VARIANT",
    "GISTIC_GENES_AMP:Q-VALUE": "GISTIC_GENES",
    "GISTIC_GENES_DEL:Q-VALUE": "GISTIC_GENES",
    "MUTSIG:Q-VALUE": "MUTATION_SIGNIFICANCE",
    "GENESET_SCORE:GSVA-SCORE": "GSVA_SCORES",
    "GENESET_SCORE:P-VALUE": "GSVA_PVALUES",
    "GENERIC_ASSAY:LIMIT-VALUE": "GENERIC_ASSAY_CONTINUOUS",
    "GENERIC_ASSAY:BINARY": "GENERIC_ASSAY_BINARY",
    "GENERIC_ASSAY:CATEGORICAL": "GENERIC_ASSAY_CATEGORICAL",
}
"""The type of a meta file by its ``<genetic_alteration_type>:<datatype>`` (C:651-689), for each
of ``schemas.PAIRS`` (the pack's tests hold that the keys are those pairs). The types are named
as C:55-85 names them."""
RESOURCE_TYPES: Mapping[str, str] = {
    "PATIENT": "PATIENT_RESOURCES",
    "SAMPLE": "SAMPLE_RESOURCES",
    "STUDY": "STUDY_RESOURCES",
    "DEFINITION": "RESOURCES_DEFINITION",
}
"""The type of a resource meta file by its ``resource_type`` (C:717-727)."""
STUDY = "STUDY"
CANCER_TYPE = "CANCER_TYPE"
SAMPLE = "SAMPLE_ATTRIBUTES"
PATIENT = "PATIENT_ATTRIBUTES"
CLINICAL = frozenset({PATIENT, SAMPLE})
"""The clinical types, whose data files M4.2a imports."""

UNKNOWN_TYPE = "has no meta file type cBioPortal defines"


def classify(found: Mapping[str, str], name: str) -> str:
    """The type of the meta file ``name`` (C:691-729), in its order: a
    ``genetic_alteration_type`` and ``datatype`` pair of ``PAIR_TYPES``; else, with both
    ``cancer_study_identifier`` and ``type_of_cancer``, the study meta; else, with
    ``type_of_cancer``, a cancer-type meta; else, with ``cancer_study_identifier`` and a
    ``resource_type`` of ``RESOURCE_TYPES``, a resource meta. A clinical meta that holds
    ``type_of_cancer`` is therefore clinical. Anything else is ``META_FILE``; stricter for an
    unknown ``resource_type``, which cBioPortal skips without an error (D409)."""
    if "genetic_alteration_type" in found and "datatype" in found:
        kind = PAIR_TYPES.get(f"{found['genetic_alteration_type']}:{found['datatype']}")
        if kind is None:
            raise entry_refused("META_FILE", name, UNKNOWN_TYPE)
        return kind
    if "cancer_study_identifier" in found and "type_of_cancer" in found:
        return STUDY
    if "type_of_cancer" in found:
        return CANCER_TYPE
    if "cancer_study_identifier" in found and "resource_type" in found:
        kind = RESOURCE_TYPES.get(found["resource_type"])
        if kind is not None:
            return kind
    raise entry_refused("META_FILE", name, UNKNOWN_TYPE)


_PROFILE = (
    "cancer_study_identifier",
    "genetic_alteration_type",
    "datatype",
    "stable_id",
    "show_profile_in_analysis_tab",
    "profile_name",
    "profile_description",
    "data_filename",
)
_CLINICAL = ("cancer_study_identifier", "genetic_alteration_type", "datatype", "data_filename")
_GENERIC_ASSAY = (
    "cancer_study_identifier",
    "genetic_alteration_type",
    "generic_assay_type",
    "datatype",
    "stable_id",
    "profile_name",
    "profile_description",
    "data_filename",
    "show_profile_in_analysis_tab",
)
_RESOURCE = ("cancer_study_identifier", "resource_type", "data_filename")

MANDATORY: Mapping[str, tuple[str, ...]] = {
    "CANCER_TYPE": ("genetic_alteration_type", "datatype", "data_filename"),  # C:95
    "STUDY": ("cancer_study_identifier", "type_of_cancer", "name", "description"),  # C:100
    "SAMPLE_ATTRIBUTES": _CLINICAL,  # C:112
    "PATIENT_ATTRIBUTES": _CLINICAL,  # C:118
    "CNA_DISCRETE": _PROFILE,  # C:124
    "CNA_DISCRETE_LONG": _PROFILE,  # C:136
    "CNA_LOG2": _PROFILE,  # C:148
    "CNA_CONTINUOUS": _PROFILE,  # C:159
    "SEG": (  # C:170
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "reference_genome_id",
        "data_filename",
        "description",
    ),
    "MUTATION": _PROFILE,  # C:178
    "MUTATION_UNCALLED": (  # C:193
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "EXPRESSION": _PROFILE,  # C:208
    "METHYLATION": _PROFILE,  # C:220
    "PROTEIN": _PROFILE,  # C:231
    "GISTIC_GENES": (  # C:242
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "reference_genome_id",
        "data_filename",
    ),
    "TIMELINE": _CLINICAL,  # C:249
    "MUTATION_SIGNIFICANCE": _CLINICAL,  # C:263
    "GENE_PANEL_MATRIX": _CLINICAL,  # C:269
    "GSVA_PVALUES": (  # C:275
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "source_stable_id",
        "profile_name",
        "profile_description",
        "data_filename",
        "geneset_def_version",
    ),
    "GSVA_SCORES": (  # C:286
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "source_stable_id",
        "profile_name",
        "profile_description",
        "data_filename",
        "show_profile_in_analysis_tab",
        "geneset_def_version",
    ),
    "GENERIC_ASSAY_CONTINUOUS": _GENERIC_ASSAY,  # C:298
    "GENERIC_ASSAY_BINARY": _GENERIC_ASSAY,  # C:313
    "GENERIC_ASSAY_CATEGORICAL": _GENERIC_ASSAY,  # C:326
    "STRUCTURAL_VARIANT": _PROFILE,  # C:339
    "SAMPLE_RESOURCES": _RESOURCE,  # C:351
    "PATIENT_RESOURCES": _RESOURCE,  # C:356
    "STUDY_RESOURCES": _RESOURCE,  # C:361
    "RESOURCES_DEFINITION": _RESOURCE,  # C:366
}
"""The keys each type's meta file must hold (C:94-371, the members marked mandatory; checked at
C:882-893), in C's order. A key present with an empty value is held."""


def check_mandatory(kind: str, found: Mapping[str, str], name: str) -> None:
    """``META_FIELD`` for the first key of ``MANDATORY[kind]`` the meta file lacks, named by the
    key (a constant)."""
    for key in MANDATORY[kind]:
        if key not in found:
            raise entry_refused("META_FIELD", name, f"lacks the key {key}")


# --- stable_id (V:4755-4774) ---------------------------------------------------------------------

STABLE_ID_CHARACTERS: frozenset[str] = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._()[]',+-:;"
)
"""The characters a ``stable_id`` may hold (V:95, the complement of its class)."""


def stable_id(value: str) -> str | None:
    """A meta file's ``stable_id``, stripped, if it is not empty and holds only
    ``STABLE_ID_CHARACTERS`` (V:4757-4765); otherwise ``None``."""
    stripped = value.strip()
    if stripped == "" or not all(character in STABLE_ID_CHARACTERS for character in stripped):
        return None
    return stripped


# --- The study meta's values (C:939-948; V:4783-4842) --------------------------------------------

STUDY_ID = re.compile(r"[a-z0-9_]+")
"""A ``cancer_study_identifier``'s form, matched whole (V:98, V:4793)."""
PMID = re.compile(r"[1-9][0-9]{0,8}")
"""A PubMed id: 1 to 9 ASCII digits, the first not ``0``. Stricter: cBioPortal takes any token
Python's ``int()`` reads (V:4836-4842), such as ``0``, ``+3``, ``1_0`` or ``٣`` (D409)."""
GENOMES = frozenset({"hg19", "hg38", "mm10"})
"""The ``reference_genome`` values cBioPortal knows (V:4732-4736, V:4811)."""

MOST: Mapping[str, int] = {
    "cancer_study_identifier": 200,
    "name": 255,
    "description": 1_024,
    "citation": 200,
    "pmid": 1_024,
    "groups": 200,
    "short_name": 64,
}
"""Each value's most characters (C:939-948), in its order. Stricter for
``cancer_study_identifier``, which C caps at 255 (the pack's schema caps it at 200)."""
TYPE_OF_CANCER_MOST = 63
"""C:940's cap; the OncoTree form caps it at 32 (stricter, D409)."""
OPTIONAL_TEXT = frozenset({"name", "description", "citation", "groups", "short_name"})
"""The values whose empty value means no value."""


@dataclass(frozen=True)
class StudyFields:
    """The study meta's values, each one ``written`` checked; ``None`` where the value is absent
    or empty. They are declared text (D271), served as written (D409)."""

    cancer_study_identifier: Checked
    type_of_cancer: Checked
    name: Checked | None = None
    description: Checked | None = None
    citation: Checked | None = None
    pmid: Checked | None = None
    groups: Checked | None = None
    short_name: Checked | None = None
    reference_genome: Checked | None = None


def _value_refused(name: str, key: str) -> Refused:
    return entry_refused("META_VALUE", name, f"holds a {key} that M4.2a does not accept")


def _pmid(value: str) -> bool:
    """Whether ``value`` is a comma-separated list of PubMed ids with no whitespace inside
    (V:4829-4842): each token, stripped, a ``PMID``."""
    if len("".join(value.split())) != len(value.strip()):
        return False
    return all(PMID.fullmatch(token.strip()) is not None for token in value.split(","))


def study_fields(found: Mapping[str, str], name: str) -> StudyFields:
    """The study meta ``name``'s values, checked: every one by ``written`` first, then each by its
    own rule. A value refused is ``META_VALUE``, naming the key."""
    checked: dict[str, Checked] = {}
    for key, most in MOST.items():
        if key not in found:
            continue
        value = found[key]
        if value == "" and key in OPTIONAL_TEXT:
            continue
        kept = written(value, most)
        if kept is None:
            raise _value_refused(name, key)
        checked[key] = kept
    study = checked.get("cancer_study_identifier")
    if study is None or STUDY_ID.fullmatch(study) is None:
        raise _value_refused(name, "cancer_study_identifier")
    cancer = written(found["type_of_cancer"], TYPE_OF_CANCER_MOST)
    if cancer is None or not oncotree(cancer):
        raise _value_refused(name, "type_of_cancer")
    pmid = checked.get("pmid")
    if pmid is not None and not _pmid(pmid):
        raise _value_refused(name, "pmid")
    genome: Checked | None = None
    if "reference_genome" in found:
        genome = written(found["reference_genome"], 4)
        if genome is None or genome not in GENOMES:
            raise _value_refused(name, "reference_genome")
    return StudyFields(
        cancer_study_identifier=study,
        type_of_cancer=cancer,
        name=checked.get("name"),
        description=checked.get("description"),
        citation=checked.get("citation"),
        pmid=pmid,
        groups=checked.get("groups"),
        short_name=checked.get("short_name"),
        reference_genome=genome,
    )


# --- The clinical role's file names (D409, P0) ---------------------------------------------------

NAME_BYTES_MOST = 255
"""The most UTF-8 bytes a clinical meta's ``data_filename`` may have (Linux's ``NAME_MAX``)."""


def file_name(value: str) -> str | None:
    """P0: the normal form of a clinical meta's ``data_filename``, or ``None`` if it is not a
    file name M4.2a accepts: over ``NAME_BYTES_MOST`` bytes in UTF-8, a NUL, absolute, a ``..``
    component, ending in ``/`` or ``/.``, or normalising to ``.`` (the empty value among them).
    The bytes are counted before anything else is read; the value is normalised once."""
    if len(value.encode("utf-8", "surrogatepass")) > NAME_BYTES_MOST:
        return None
    if "\0" in value or value.startswith("/") or ".." in value.split("/"):
        return None
    if value.endswith(("/", "/.")):
        return None
    normal = posixpath.normpath(value)
    if normal == ".":
        return None
    return normal


__all__ = [
    "CLINICAL",
    "MANDATORY",
    "META_MOST",
    "PAIR_TYPES",
    "PATIENT",
    "RESOURCE_TYPES",
    "SAMPLE",
    "STUDY",
    "StudyFields",
    "check_mandatory",
    "classify",
    "excluded",
    "file_name",
    "meta_named",
    "parse",
    "stable_id",
    "study_fields",
]

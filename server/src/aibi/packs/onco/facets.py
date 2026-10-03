"""The oncology pack's catalogue facet (SPEC §10.1, §11.1, D273).

It reads declared extension members alone, never rows: ``type_of_cancer`` and
``reference_genome`` from the dataset's extension, and ``profiles``, the distinct ``meta`` pairs
of its tables, sorted, with the clinical and cancer-type files left out (they are not profiles).
A facet with no value is left out, never given as an empty list. What it shows is declared
descriptor content, served as written under every *k* (D271); what keeps it so is §10.1's rule
that no importer writes values computed from the rows into an extension member.
"""

from aibi.core.schema.descriptors import DatasetDescriptor, TableDescriptor
from aibi.core.schema.pack_api import ReleaseView

PACK_ID = "onco"
NOT_PROFILES = ("CLINICAL:", "CANCER_TYPE:")
"""The ``meta`` pairs that name no molecular profile, by their ``genetic_alteration_type``."""
DATASET_FACETS = ("type_of_cancer", "reference_genome")


def facet(release: ReleaseView) -> dict[str, list[str]]:
    """The release's facets, each a non-empty list."""
    found: dict[str, list[str]] = {}
    profiles: set[str] = set()
    for descriptor in release.descriptors.values():
        members = descriptor.extensions.get(PACK_ID)
        if members is None:
            continue
        if isinstance(descriptor, DatasetDescriptor):
            for name in DATASET_FACETS:
                value = members.get(name)
                if isinstance(value, str) and value:
                    found[name] = [value]
        elif isinstance(descriptor, TableDescriptor):
            meta = members.get("meta")
            if isinstance(meta, str) and meta and not meta.startswith(NOT_PROFILES):
                profiles.add(meta)
    if profiles:
        found["profiles"] = sorted(profiles)
    return found

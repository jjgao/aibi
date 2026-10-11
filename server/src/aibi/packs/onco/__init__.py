"""The oncology pack (SPEC §10.2): its concepts, ontology systems, descriptor extensions and
catalogue facet. A deployment installs it by naming it in its configuration,
``[packs] modules = ["aibi.packs.onco"]`` (D404); nothing installs it by default."""

from aibi.core.schema.pack_api import Pack, PackManifest
from aibi.packs.onco.concepts import CONCEPTS
from aibi.packs.onco.facets import PACK_ID, facet
from aibi.packs.onco.ontology import SYSTEMS
from aibi.packs.onco.schemas import SCHEMAS

PACK = Pack(
    manifest=PackManifest(id=PACK_ID, version="0.1.0", results_version=1, requires_core=">=0.0.1"),
    concepts=CONCEPTS,
    ontology_systems=SYSTEMS,
    extension_schemas=SCHEMAS,
    facet=facet,
)

__all__ = ["PACK"]

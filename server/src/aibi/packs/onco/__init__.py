"""The oncology pack (SPEC §10.2): its concepts, ontology systems, descriptor extensions,
catalogue facet, the importer of a cBioPortal study's clinical files and its validator (M4.2a-1b,
D409), and its curation proposer of survival endpoints (``endpoints.propose``, M4.2a-2, D439). A
deployment installs it by naming it in its configuration, ``[packs] modules = ["aibi.packs.onco"]``
(D404); nothing installs it by default, and an operator imports a study with it by naming it
(``aibi import <dataset> <directory> --pack onco``)."""

from aibi.core.schema.pack_api import Pack, PackManifest
from aibi.packs.onco.concepts import CONCEPTS
from aibi.packs.onco.endpoints import propose
from aibi.packs.onco.facets import PACK_ID, facet
from aibi.packs.onco.importer import VERSION, OncoImporter
from aibi.packs.onco.ontology import SYSTEMS
from aibi.packs.onco.schemas import SCHEMAS
from aibi.packs.onco.validators import OncoValidator

PACK = Pack(
    manifest=PackManifest(id=PACK_ID, version=VERSION, results_version=1, requires_core=">=0.0.1"),
    concepts=CONCEPTS,
    ontology_systems=SYSTEMS,
    extension_schemas=SCHEMAS,
    importer=OncoImporter(),
    validator=OncoValidator(),
    facet=facet,
    proposer=propose,
)

__all__ = ["PACK"]

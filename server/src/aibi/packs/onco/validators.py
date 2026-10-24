"""The oncology pack's validator (SPEC §10.1, D407, D409).

- ``validate_source`` refuses nothing: discovery and the clinical files' reader refuse what they
  must as the importer reads, and the core gives this validator every import that consults the
  pack, another importer's included, so it reads none of them.
- ``validate_descriptors`` reads a release's dataset descriptor alone, and refuses
  ``onco.TYPE_OF_CANCER`` at ``/dataset/extensions/onco/type_of_cancer`` when the member is
  present and not a code the ``OncoTree`` validator accepts (``ontology.oncotree``: its form, as
  written, any case). It binds an operator's edit in a session as well as an import. The member is
  served as written (D407): the study's own spelling, ``mixed`` or ``MIXED``, is kept.

Both are total: they read only exact built-in values, and catch nothing.
"""

from aibi.core.schema.descriptors import DatasetDescriptor
from aibi.core.schema.output import text
from aibi.core.schema.pack_api import ImportResult, ImportSource, Refusals, ReleaseView
from aibi.core.schema.refusals import Refusal
from aibi.packs.onco.facets import PACK_ID
from aibi.packs.onco.ontology import oncotree

TYPE_OF_CANCER = "type_of_cancer"
POINTER = f"/dataset/extensions/{PACK_ID}/{TYPE_OF_CANCER}"


class OncoValidator:
    """``PACK.validator`` (the module's docstring)."""

    def validate_source(self, source: ImportSource, result: ImportResult) -> Refusals:
        return []

    def validate_descriptors(self, release: ReleaseView) -> Refusals:
        dataset = release.descriptors.get("dataset")
        if not isinstance(dataset, DatasetDescriptor):
            return []
        members = dataset.extensions.get(PACK_ID)
        if members is None or TYPE_OF_CANCER not in members:
            return []
        value = members[TYPE_OF_CANCER]
        if type(value) is str and oncotree(value):
            return []
        return [
            Refusal(
                code=f"{PACK_ID}.TYPE_OF_CANCER",
                path=POINTER,
                message=[text("The type of cancer is not an OncoTree code by its form")],
            )
        ]


__all__ = ["POINTER", "OncoValidator"]

"""A registry entry as an output (SPEC §9.1, §11.1; D417): ``list_analyses`` lists an analysis
descriptor's members typed, so that the JSON is the descriptor's.

These models are kept apart from ``catalog`` so that the pack registry, which ``catalog``'s
modules import, can check at registration that an entry it holds lists (``analysis_entry``).
"""

from typing import Annotated, Literal, cast

from pydantic import ConfigDict, Field, JsonValue

from aibi.core.schema.descriptors import (
    AnalysisDescriptor,
    Datatype,
    Label,
    NameText,
    PositiveInt,
    SemVer,
    Text,
)
from aibi.core.schema.ids import AnalysisId, Identifier, PackName
from aibi.core.schema.limits import ENTRIES, MAX_ENTRIES, LimitName, map_cap
from aibi.core.schema.output import (
    DATA_MARK,
    Count,
    FiniteExtensions,
    FiniteJsonObject,
    Output,
)


class RequirementOut(Output):
    """What an analysis needs, as ``list_analyses`` lists it (§9.1): a descriptor's
    ``Requirement`` as an output. A pack's text, so data (A6)."""

    model_config = ConfigDict(json_schema_extra=DATA_MARK)

    role: Identifier
    kind: Literal["endpoint", "column", "table"] | None = None
    on: Literal["unit"] | None = None
    datatype: Datatype | None = None
    min: Count | None = None
    max: PositiveInt | None = None
    predicate: PackName | None = None


class LibraryOut(Output):
    model_config = ConfigDict(json_schema_extra=DATA_MARK)

    name: NameText
    version: NameText


class CrossDatasetOut(Output):
    model_config = ConfigDict(json_schema_extra=DATA_MARK)

    method: Text


class RandomnessOut(Output):
    model_config = ConfigDict(json_schema_extra=DATA_MARK)

    seeded: bool
    replicates: PositiveInt


class FieldsOut(Output):
    """An analysis descriptor's ``fields``, as an output (§9.1). ``params`` and ``returns`` are
    JSON Schemas a pack or the core wrote: data, never instructions (A6)."""

    model_config = ConfigDict(json_schema_extra=DATA_MARK)

    requires: Annotated[list[RequirementOut], Field(max_length=MAX_ENTRIES), LimitName(ENTRIES)]
    params: FiniteJsonObject
    returns: FiniteJsonObject
    methods: Annotated[
        dict[str, str],
        Field(max_length=MAX_ENTRIES),
        LimitName(ENTRIES),
        map_cap(MAX_ENTRIES),
    ]
    library: LibraryOut | None = None
    assumptions: Annotated[list[str], Field(max_length=MAX_ENTRIES), LimitName(ENTRIES)]
    uses_reference: bool
    assumes_independent_groups: bool
    cross_dataset: CrossDatasetOut | None
    randomness: RandomnessOut | None = None
    caveats: Annotated[list[str], Field(max_length=MAX_ENTRIES), LimitName(ENTRIES)]
    min_group_n: PositiveInt | None = None
    min_events: PositiveInt | None = None


class AnalysisEntry(Output):
    """A registry entry as ``list_analyses`` lists it (§9.1, D417): an analysis descriptor's
    members, in the envelope's order, as an output. Every string in it is the core's or a
    pack's data, never an instruction (A6): the entry is marked as data as a whole. Its JSON is
    the descriptor's: ``extensions`` and ``curation`` are written only when the descriptor was
    given them."""

    model_config = ConfigDict(json_schema_extra=DATA_MARK)

    label: Label
    definition: Text | None = None
    provenance: FiniteJsonObject | None = None
    extensions: FiniteExtensions | None = None
    curation: FiniteJsonObject | None = None
    kind: Literal["analysis"]
    id: AnalysisId
    version: SemVer
    fields: FieldsOut


def analysis_entry(descriptor: AnalysisDescriptor) -> AnalysisEntry:
    """A registry entry as ``list_analyses`` gives it: the descriptor's own members, typed, so
    that the JSON is the descriptor's (D417). Raises ``ValidationError`` when a type of the
    entry is narrower than what the registry holds."""
    dumped = cast(dict[str, JsonValue], descriptor.model_dump(mode="json"))
    return AnalysisEntry.model_validate(dumped)


__all__ = [
    "AnalysisEntry",
    "CrossDatasetOut",
    "FieldsOut",
    "LibraryOut",
    "RandomnessOut",
    "RequirementOut",
    "analysis_entry",
]

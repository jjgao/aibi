"""The oncology pack's curation proposer: survival endpoints (SPEC §10.1, §10.2, D249, D439,
M4.2a-2).

``propose`` is ``PACK.proposer``. It reads a release's descriptors, never its rows, and proposes
one whole ``ep:`` endpoint descriptor for each of cBioPortal's four survival pairs a patient file
holds (``ENDPOINTS``: OS, DFS, PFS, DSS), for an operator to accept in a session:

- **The patient table** is the one table whose ``onco`` extension's ``meta`` is
  ``CLINICAL:PATIENT_ATTRIBUTES``, with a primary key; with none, or more than one, nothing is
  proposed.
- **A pair's columns** are found by ``fields.source.original_name``, never by id (a header that
  folds to ``os_status`` can take that id and push the real ``OS_STATUS`` to ``os_status_2``,
  D238): exactly one column of the patient table named ``<X>_STATUS`` and exactly one named
  ``<X>_MONTHS``, the months column still a ``time_offset`` (the importer writes it so,
  ``attributes.TIME_OFFSETS``).
- **A pair is skipped** when either column is missing or named twice, the months column is not a
  ``time_offset``, or an endpoint of the release has the id ``ep:<x>``, maps to the pair's concept,
  or reads the same table and time column (so that accepting one never makes a second usable
  endpoint on a clock an operator already declared).
- **What is proposed:** ``table``, ``time_column``, ``status_column`` and ``maps_to``; for OS and
  DFS also ``event_coding``, from the validator's own accept-sets (``attributes.OS_VALUES``,
  ``DFS_VALUES``): ``1:`` an event, ``0:`` censored, a value without a prefix on its prefixed
  partner's side, each side sorted (a ``frozenset``'s order changes with the hash seed). PFS and
  DSS get no ``event_coding``: the format fixes only their ``0:``/``1:`` prefixes, and their labels
  differ by study, so such an endpoint cannot be used until an operator declares one.
  ``time_origin`` and ``entry`` are never proposed: the format does not fix a study's clock or
  when its patients enter, and a study's own definitions differ (``msk_chord_2024`` counts from
  the first sequenced sample).
- **The evidence** is fixed text per endpoint: no definition, label or cell reaches it, so a
  changed definition makes no new proposal and a rejection stays remembered (D248, D249). The
  only strings in a proposal that the data chose are the two column ids, each named with the
  original name it was found by.

A proposal names columns by id, and a re-import can give a freed id to another header (D238), so
an open proposal made against an earlier release can read another column after it: an operator
checks or rejects a stale proposal before accepting it (D439).
"""

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import NamedTuple

from pydantic import JsonValue

from aibi.core.schema.descriptors import (
    ColumnDescriptor,
    Descriptor,
    EndpointDescriptor,
    TableDescriptor,
)
from aibi.core.schema.pack_api import Proposal, ReleaseView
from aibi.packs.onco.attributes import DFS_VALUES, OS_VALUES
from aibi.packs.onco.facets import PACK_ID
from aibi.packs.onco.meta import PATIENT

PATIENT_META = f"CLINICAL:{PATIENT}"
EVENT = "1"
CENSORED = "0"


def coding(values: frozenset[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(event, censored)`` of a status's accepted values: a value written ``1:<label>`` is an
    event and ``0:<label>`` censored; a value without the prefix is on the side of the one
    prefixed value whose label it is. Each side is in code point order. A value with no side, or
    two, raises ``ValueError`` (the pack's own constants are tested to have none)."""
    sides: dict[str, list[str]] = {EVENT: [], CENSORED: []}
    for value in sorted(values):
        head, colon, _ = value.partition(":")
        if colon and head in sides:
            sides[head].append(value)
            continue
        partners = sorted(
            {
                other.partition(":")[0]
                for other in values
                if other.partition(":")[0] in sides and other.partition(":")[2] == value
            }
        )
        if len(partners) != 1:
            raise ValueError("a status value with no side, or two")
        sides[partners[0]].append(value)
    return tuple(sides[EVENT]), tuple(sides[CENSORED])


class Endpoint(NamedTuple):
    """One of cBioPortal's survival pairs, as the proposer writes it."""

    name: str
    """The endpoint's ident, ``ep:<name>``."""
    prefix: str
    """The columns' names: ``<prefix>_STATUS`` and ``<prefix>_MONTHS``."""
    label: str
    """The endpoint concept's label."""
    concept: str
    coding: tuple[tuple[str, ...], tuple[str, ...]] | None
    """``(event, censored)``, or ``None`` where the format fixes none."""


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint("os", "OS", "Overall survival", "onco:endpoint.os", coding(OS_VALUES)),
    Endpoint("dfs", "DFS", "Disease-free survival", "onco:endpoint.dfs", coding(DFS_VALUES)),
    Endpoint("pfs", "PFS", "Progression-free survival", "onco:endpoint.pfs", None),
    Endpoint("dss", "DSS", "Disease-specific survival", "onco:endpoint.dss", None),
)
"""The four pairs, in the order they are proposed; their months columns are
``attributes.TIME_OFFSETS``'s."""

READ = (
    "cBioPortal's clinical file format pairs a patient's {prefix}_STATUS with {prefix}_MONTHS. "
    "This endpoint reads {table}.{months}, the column named {prefix}_MONTHS, as its time (months), "
    "and {table}.{status}, the column named {prefix}_STATUS, as its status. "
)
CODED = (
    "Its event coding is the format's own status values: 1: an event, 0: censored, and a value "
    "without its prefix on its prefixed partner's side. "
)
UNCODED = (
    "The format fixes only the 0: and 1: prefixes of {prefix}_STATUS, and its labels differ by "
    "study, so the proposal declares no event_coding: the endpoint cannot be used until an "
    "operator declares event_coding from the column's values. "
)
UNDECLARED = (
    "The proposal declares no time_origin and no entry: the format fixes neither a study's clock "
    "nor when its patients enter, and studies count from different dates. Read the definitions "
    "of the two columns in the catalogue, and declare time_origin and entry in a session: "
    "time_origin is a time origin the pack or the core defines, such as the pack's "
    "onco:origin.diagnosis, onco:origin.specimen_collection, onco:origin.treatment_start and "
    "onco:origin.first_sequencing. Check "
    "that both columns still carry the names above before accepting a proposal made against an "
    "earlier release: a re-import can give a column's id to another header."
)
EVIDENCE: Mapping[bool, str] = MappingProxyType({True: READ + CODED, False: READ + UNCODED})
"""The evidence's first part, by whether the endpoint has an event coding; ``UNDECLARED`` ends
every one."""


def _members(descriptor: Descriptor) -> Mapping[str, JsonValue]:
    members = descriptor.extensions.get(PACK_ID)
    return members if isinstance(members, Mapping) else MappingProxyType({})


def _patient_table(descriptors: Mapping[str, Descriptor]) -> TableDescriptor | None:
    tables = [
        descriptor
        for descriptor in descriptors.values()
        if isinstance(descriptor, TableDescriptor)
        and _members(descriptor).get("meta") == PATIENT_META
    ]
    if len(tables) != 1 or not tables[0].fields.primary_key:
        return None
    return tables[0]


def _named(descriptors: Mapping[str, Descriptor], table: str, name: str) -> ColumnDescriptor | None:
    """The one column of ``table`` whose original name is ``name``, or ``None``."""
    found = [
        descriptor
        for descriptor in descriptors.values()
        if isinstance(descriptor, ColumnDescriptor)
        and descriptor.id.split(".", 1)[0] == table
        and descriptor.fields.source is not None
        and descriptor.fields.source.original_name == name
    ]
    return found[0] if len(found) == 1 else None


def _declared(
    endpoints: Sequence[EndpointDescriptor], endpoint: Endpoint, table: str, time_column: str
) -> bool:
    """Whether an endpoint of the release already has the pair's id, maps to its concept, or
    reads the same table and time column."""
    for other in endpoints:
        fields = other.fields
        if other.id == f"ep:{endpoint.name}":
            return True
        if fields.maps_to is not None and fields.maps_to.concept == endpoint.concept:
            return True
        if fields.table == table and fields.time_column == time_column:
            return True
    return False


def _proposal(endpoint: Endpoint, table: str, status: str, months: str) -> Proposal:
    fields: dict[str, JsonValue] = {
        "table": table,
        "time_column": months,
        "status_column": status,
        "maps_to": {"concept": endpoint.concept},
    }
    if endpoint.coding is not None:
        event, censored = endpoint.coding
        fields["event_coding"] = {"event": list(event), "censored": list(censored)}
    evidence = EVIDENCE[endpoint.coding is not None].format(
        prefix=endpoint.prefix, table=table, months=months, status=status
    )
    identifier = f"ep:{endpoint.name}"
    return Proposal(
        identifier,
        "",
        {"kind": "endpoint", "id": identifier, "label": endpoint.label, "fields": fields},
        evidence=evidence + UNDECLARED,
    )


def propose(release: ReleaseView) -> tuple[Proposal, ...]:
    """The release's endpoint proposals, in ``ENDPOINTS``' order."""
    descriptors = release.descriptors
    patients = _patient_table(descriptors)
    if patients is None:
        return ()
    table = patients.id
    endpoints = [d for d in descriptors.values() if isinstance(d, EndpointDescriptor)]
    found: list[Proposal] = []
    for endpoint in ENDPOINTS:
        status = _named(descriptors, table, f"{endpoint.prefix}_STATUS")
        months = _named(descriptors, table, f"{endpoint.prefix}_MONTHS")
        if status is None or months is None or months.fields.datatype != "time_offset":
            continue
        time_column = months.id.split(".", 1)[1]
        if _declared(endpoints, endpoint, table, time_column):
            continue
        found.append(_proposal(endpoint, table, status.id.split(".", 1)[1], time_column))
    return tuple(found)


__all__ = ["ENDPOINTS", "Endpoint", "coding", "propose"]

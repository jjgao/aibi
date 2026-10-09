"""What the server gives alike to every call, and its bounds (SPEC §11.1, D421).

Two listings depend on the registry alone, the core's analyses and the installed packs: the
entries of ``list_analyses`` (``Analyses.entries``) and ``list_leaf_kinds``
(``leaf_kind_listing``). The service functions serve them from these functions, and the loader
measures them from the same functions once, when the packs are registered
(``served_problems``, which ``aibi-server check`` and ``serve`` both reach through
``api.packs``), so that what is measured is what is served. Each listing is bounded on its own,
never summed with the other:

- **JSON values**: at most ``MAX_SERVED_VALUES``, counted over the listing's served JSON, every
  value of every member counted, containers included (``json_counts``), the core's entries and
  the listing's own members with the packs' data. The work of a call grows with its values and
  members more than with its bytes: an ``enum`` of many small numbers, or many tiny models.
- **Containers**: at most ``MAX_SERVED_CONTAINERS`` of those values objects or arrays, at every
  depth (``json_counts``): building, checking and dumping a container costs 2 to 10 times a
  scalar (a nested array or object the most), so a listing of containers alone at the value cap
  would cost ten times one of numbers.
- **Bytes**: at most ``MAX_SERVED_BYTES`` of its form over MCP, the larger of the two
  transports: a tool's result carries the JSON body as ``structuredContent`` and again as one
  JSON string, its text (``form_bytes``); over HTTP the body alone is sent.

Over either, the start fails with a ``PackError``-like problem naming the limit, the total and
every share: the core's, each installed pack's, in pack id order, and the listing's own
framing, which sum to the total exactly, so that the order of ``[packs] modules`` never
decides which pack is named. Nothing here is cached: the bounds make each call's work bounded.

A ``Catalog`` built directly from a ``PackRegistry`` (tests, an embedding) bypasses this check;
the server's registry is always the loader's.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import pydantic_core
from pydantic import JsonValue

from aibi.core.analyses.registry import CORE, Analyses
from aibi.core.schema.catalog import AnalysisListing
from aibi.core.schema.entries import LeafKindListing, leaf_kind_entry
from aibi.core.schema.limits import (
    MAX_SERVED_BYTES,
    MAX_SERVED_CONTAINERS,
    MAX_SERVED_VALUES,
)
from aibi.core.schema.output import Output
from aibi.core.schema.pack_api import PackRegistry


def leaf_kind_listing(registry: PackRegistry | None) -> LeafKindListing:
    """Every registered leaf kind, by kind, as ``list_leaf_kinds`` gives it (D421): its pack's
    version read once per pack, and its schema as registered (``PackRegistry.leaf_schemas``)."""
    if registry is None:
        return LeafKindListing(kinds=[])
    versions = {pack_id: registry.manifest(pack_id).version for pack_id in registry.ids}
    return LeafKindListing(
        kinds=[
            leaf_kind_entry(kind, versions[kind.partition(".")[0]], cast(JsonValue, schema))
            for kind, schema in registry.leaf_schemas().items()
        ]
    )


def form_bytes(text: str) -> int:
    """The bytes of a tool's output over MCP, given its JSON text: the text in UTF-8, as the
    result's ``structuredContent`` carries it, and the text as one JSON string,
    ``pydantic_core.to_json(text)`` (escaped as the SDK escapes it: ``json.dumps`` with its
    default ``ensure_ascii`` would count an astral character as 12 bytes, not 4), as the
    result's text content carries it (D421). The JSON-RPC envelope around them (108 bytes at
    ``id`` 1, more for a longer ``id``) is the client's, and not counted; over HTTP the text
    alone is sent, so this bounds both transports."""
    return len(text.encode("utf-8")) + len(pydantic_core.to_json(text))


def json_counts(value: JsonValue) -> tuple[int, int]:
    """The JSON values of ``value``, as an allowance counts them (D343): each object, array,
    string, number, boolean and ``null``, containers included, a key no value; and of them the
    containers, each object and array at every depth."""
    count = containers = 0
    pending: list[JsonValue] = [value]
    while pending:
        found = pending.pop()
        count += 1
        if isinstance(found, dict):
            containers += 1
            pending.extend(found.values())
        elif isinstance(found, list):
            containers += 1
            pending.extend(found)
    return count, containers


@dataclass(frozen=True)
class Share:
    """What one owner's items of a listing add to its served form: ``owner`` names it (the
    core, a pack as a registry's problem names it, or the listing itself)."""

    owner: str
    values: int
    containers: int
    bytes: int


@dataclass(frozen=True)
class Measured:
    """A listing's served form, measured: its totals, and the shares that sum to them exactly,
    the core's first, then each pack's in pack id order, then the listing's own framing."""

    tool: str
    values: int
    containers: int
    bytes: int
    shares: tuple[Share, ...]


CORE_OWNER = "the core"
FRAMING = "the listing itself"


def _measured(
    tool: str, empty: Output, items: Sequence[tuple[str, Output]], owners: Sequence[str]
) -> Measured:
    """The served form of a listing that is ``empty`` with ``items`` in its one list, measured
    item by item: each item's values and bytes as they are inside the listing (its JSON text,
    escaped once more for the MCP text, without the string's quotes), ``owners`` every owner in
    order, and the listing's own framing (its JSON without items, and a comma between two
    items, in the body and in the text)."""
    values = dict.fromkeys(owners, 0)
    containers = dict.fromkeys(owners, 0)
    sizes = dict.fromkeys(owners, 0)
    for owner, item in items:
        text = item.model_dump_json()
        found, nested = json_counts(json.loads(text))
        values[owner] += found
        containers[owner] += nested
        sizes[owner] += form_bytes(text) - 2
    bare = empty.model_dump_json()
    found, nested = json_counts(json.loads(bare))
    framing = Share(FRAMING, found, nested, form_bytes(bare) + 2 * max(len(items) - 1, 0))
    shares = (
        *(Share(owner, values[owner], containers[owner], sizes[owner]) for owner in owners),
        framing,
    )
    return Measured(
        tool,
        sum(share.values for share in shares),
        sum(share.containers for share in shares),
        sum(share.bytes for share in shares),
        shares,
    )


def measured(registry: PackRegistry | None) -> tuple[Measured, Measured]:
    """The served forms of ``list_analyses``' entries and of ``list_leaf_kinds`` over the core
    and ``registry``'s packs, built by the functions that serve them."""
    packs = [] if registry is None else list(registry.ids)
    labels = {} if registry is None else {pack: registry.label(pack) for pack in packs}
    owners = [CORE_OWNER, *(labels[pack] for pack in packs)]
    entries = [
        (CORE_OWNER if entry.id in CORE else labels[entry.id.partition(".")[0]], entry)
        for entry in Analyses(registry).entries()
    ]
    kinds = [(labels[entry.pack], entry) for entry in leaf_kind_listing(registry).kinds]
    return (
        _measured("list_analyses", AnalysisListing(analyses=[]), entries, owners),
        _measured("list_leaf_kinds", LeafKindListing(kinds=[]), kinds, owners),
    )


_UNITS = {
    "values": ("JSON values", MAX_SERVED_VALUES, "MAX_SERVED_VALUES"),
    "containers": ("JSON objects and arrays", MAX_SERVED_CONTAINERS, "MAX_SERVED_CONTAINERS"),
    "bytes": ("bytes of its form over MCP", MAX_SERVED_BYTES, "MAX_SERVED_BYTES"),
}
"""Each unit a listing is bounded in: how a problem names it, its limit and the limit's name."""


def _count(share: Share | Measured, unit: str) -> int:
    if unit == "values":
        return share.values
    if unit == "containers":
        return share.containers
    return share.bytes


def served_problems(registry: PackRegistry | None) -> list[str]:
    """The problems of a registry whose listings, with the core's, would serve more than
    ``MAX_SERVED_VALUES`` JSON values, ``MAX_SERVED_CONTAINERS`` objects and arrays or
    ``MAX_SERVED_BYTES`` bytes of their form over MCP, each listing on its own, each naming
    the limit, the total and every share (D421)."""
    problems: list[str] = []
    for found in measured(registry):
        for unit, (words, most, name) in _UNITS.items():
            total = _count(found, unit)
            if total <= most:
                continue
            named = ", ".join(f"{share.owner} has {_count(share, unit)}" for share in found.shares)
            problems.append(
                f"{found.tool} would serve {total} {words}, more than the {most} allowed "
                f"({name}): {named}"
            )
    return problems


__all__ = [
    "CORE_OWNER",
    "FRAMING",
    "Measured",
    "Share",
    "form_bytes",
    "json_counts",
    "leaf_kind_listing",
    "measured",
    "served_problems",
]

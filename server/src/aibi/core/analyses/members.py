"""``summary.members``: a page of the unit keys of one cohort (SPEC §8.4, §9.3, §9.5; D331–D333).

A view of it names exactly one cohort, listed in ``cohorts`` or the document's only one
(``views.parse``), and takes ``offset`` and ``limit``. Its value, at its one position, is the
unit table's key columns and the keys of the cohort's members (its units for which it is TRUE)
from the ``offset``-th, at most ``limit`` of them, each its values in key order as they are
stored, and whether more follow. Keys are ordered as step 8 of §7.6 orders an ``ids`` leaf's
members, by their RFC 8785 serialisation compared as UTF-16 code units (``engine.members``), so a
page is the same on every platform and a page's keys can be written into an ``ids`` leaf as they
are. A key's values are carried as the canonical form writes them (integers beyond ±(2^53 − 1) as
decimal strings, integral doubles as integers), text, dates and datetimes as data (§8.1).

**Accounting** (§8.1). The population is the cohort's count; ``analysed`` counts its members, none
of which is excluded, since a key column's cells are all PRESENT (§13.2).

**Disclosure** (§8.4; D332). A key is held by one unit, so no disclosure setting can protect a
list of them: two lists of cohorts each of *k* units or more give by difference the units of a
set of any size, the cohort ``X`` beside ``not X`` and every unit gives each unit's answer to
``X``, and a key under *k* is a value that one unit holds, which D329 never names. So the view is
refused under any disclosure setting, and where the dataset allows no row ids
(``ROW_IDS_NOT_ALLOWED``, ``views.checked``); ``list_members`` is never given a *k*, and refuses
one.

**Determinism** (§9.3). No statistic is computed: the keys are read as stored and ordered by their
serialisation, so the only method is the order, which the tests hold to an independent sort. The
view has no chart: a list of keys holds no number to draw (§8.5).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import cast

from aibi.core.analyses.common import CohortAt, cohort_caveats, populations
from aibi.core.engine.canonical import CanonicalCohort
from aibi.core.engine.members import Key, key_columns, page
from aibi.core.engine.resolve import UNCONFIRMED, FieldRead
from aibi.core.engine.resolved import Constant, constant_json
from aibi.core.schema.analyses import (
    KeyPart,
    MembersParams,
    MembersPosition,
    MembersValues,
    NoViewValues,
)
from aibi.core.schema.caveats import Caveat, CaveatCode
from aibi.core.schema.descriptors import AnalysisDescriptor
from aibi.core.schema.export import params_schema, values_schema
from aibi.core.schema.output import Data, Segment, data, text
from aibi.core.schema.results import Analysed, Population
from aibi.core.schema.semantics import ExclusionReason

ANALYSIS_ID = "summary.members"
VERSION = "1.0.0"
"""Bumped whenever its outputs for the same inputs change (§7.6)."""
METHODS: Mapping[str, str] = {
    "order": "Keys sorted by their RFC 8785 serialisation compared as UTF-16 code units, as the "
    "canonical form sorts an ids leaf's members (SPEC 7.6, step 8), then paged by offset and "
    "limit",
}
CAVEATS = (
    CaveatCode.UNKNOWN_EXCLUDED,
    CaveatCode.SCOPE_PARTIAL,
    CaveatCode.COVERAGE_PROPOSED,
    CaveatCode.UNCONFIRMED_SEMANTICS,
    CaveatCode.DRAFT_RELEASE,
    CaveatCode.LIFT_DIFFERS,
)
ENTRY = AnalysisDescriptor.model_validate(
    {
        "kind": "analysis",
        "id": ANALYSIS_ID,
        "version": VERSION,
        "label": "Unit keys of a cohort",
        "definition": (
            "The unit keys of exactly one cohort's members, in the canonical form's order, a "
            "page at a time by offset and limit. Refused under any disclosure setting and where "
            "the dataset allows no row ids."
        ),
        "fields": {
            "requires": [{"role": "cohorts", "min": 1, "max": 1}],
            "params": params_schema(MembersParams),
            "returns": values_schema(MembersValues),
            "methods": dict(METHODS),
            "assumptions": ["descriptive only"],
            "uses_reference": False,
            "assumes_independent_groups": False,
            "cross_dataset": None,
            "caveats": [code.value for code in CAVEATS],
        },
    }
)
"""The registry entry (§9.1): implemented directly, with no library."""


def key_part(value: Constant) -> KeyPart:
    """A key's value as the result carries it (module docstring): as the canonical form writes
    it, text, a date or a datetime as data."""
    found = constant_json(value)
    if isinstance(value, str | date):
        return Data(data=cast(str, found))
    assert isinstance(found, bool | int | float | str), "a number is written as JSON holds it"
    return found


@dataclass(frozen=True)
class Outcome:
    """A view's digested parts, before its digest is taken, and its caveats (D331)."""

    population: list[Population]
    analysed: list[Analysed]
    values: MembersValues
    caveats: list[Caveat]


def list_members(
    position: CohortAt, keys: Sequence[Key], params: MembersParams, *, k: int | None
) -> Outcome:
    """``summary.members`` over a view's one cohort (module docstring), from its members' keys in
    the canonical form's order (``engine.members``: read by SQL, or from the reference
    evaluator's truth values)."""
    if k is not None:
        raise ValueError("summary.members lists no key under a disclosure setting (D332)")
    count = position.accounting.n_true
    if len(keys) != count:
        raise ValueError("a cohort's keys are one per member")
    listed, more = page(keys, params.offset, params.limit)
    population = populations([position], None)
    columns = [
        Data(data=f"{position.cohort.resolved.unit}.{column}")
        for column in key_columns(position.cohort.resolved)
    ]
    values = MembersValues(
        positions=[
            MembersPosition(
                columns=columns,
                keys=[[key_part(value) for value in key] for key in listed],
                offset=params.offset,
                more=more,
            )
        ],
        view=NoViewValues(),
    )
    analysed = Analysed(n=count, excluded=dict.fromkeys(ExclusionReason, 0), excluded_units=0)
    caveats = cohort_caveats([position], population, None)
    return Outcome(population, [analysed], values, caveats)


def key_reads(cohort: CanonicalCohort) -> list[FieldRead]:
    """The fields a view reads to list keys that are not confirmed (§5.1): the unit table's
    ``primary_key`` and each key column's ``datatype``, which sets how its values are carried,
    undeclared where no one declared it."""
    resolved = cohort.resolved
    release = resolved.release
    found: list[FieldRead] = []
    table = release.table(resolved.unit)
    if table is not None:
        entry = table.curation.get("/fields/primary_key")
        if entry is not None and entry.status in UNCONFIRMED:
            found.append(FieldRead(table.id, "/fields/primary_key", entry.status))
    for column in key_columns(resolved):
        descriptor = release.column(resolved.unit, column)
        if descriptor is None:
            continue
        entry = descriptor.curation.get("/fields/datatype")
        if entry is not None and entry.status in UNCONFIRMED:
            found.append(FieldRead(descriptor.id, "/fields/datatype", entry.status))
        elif entry is None and descriptor.fields.datatype is None:
            found.append(FieldRead(descriptor.id, "/fields/datatype", "undeclared"))
    return found


def view_readback(cohort: CanonicalCohort, params: MembersParams) -> list[Segment]:
    """The view's readback (§7.7): a function of its canonical form and the release's
    descriptors, so the cohort is not named."""
    resolved = cohort.resolved
    found: list[Segment] = [text("The unit keys of the cohort's members, each its values of ")]
    for index, column in enumerate(key_columns(resolved)):
        if index:
            found.append(text(", "))
        found.append(data(f"{resolved.unit}.{column}"))
    found += [
        text(" in key order, sorted by their canonical serialisation; from the member at "),
        text("offset "),
        data(str(params.offset)),
        text(", at most "),
        data(str(params.limit)),
        text(" of them."),
    ]
    return found


__all__ = [
    "ANALYSIS_ID",
    "CAVEATS",
    "ENTRY",
    "METHODS",
    "VERSION",
    "Outcome",
    "key_part",
    "key_reads",
    "list_members",
    "view_readback",
]

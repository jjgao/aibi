"""A view's result envelope, from its canonical form and its analysis's outcome (SPEC §8.1;
D318).

The envelope's digested members are its cohorts (their ids, in view order, and the reference),
their population, the units analysed, the values and the caveats, as the analysis left them
after the disclosure pass; the digest is taken of them (§7.6). The rest is rendered for each
issuance: the derivation, whose ``document`` holds the canonical view and the object each of its
cohorts' ids hashes, so that every id the result names can be computed again from it; the
issuance; the source (the document as written, each leaf of the view's cohorts and predicates as
written with the keys of the clauses it became part of, and the parameters used); the
readbacks; the cohorts' names, as data; and the charts.

``NOT_ESTIMABLE`` is added here, over the values as the pass left them, whenever a number other
than a suppressed one is not estimable (§8.2); the static caveats are the view's
(``CheckedView.static_caveats``).
"""

from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

from aibi.core.analyses.charts import existence_chart
from aibi.core.analyses.existence import Outcome
from aibi.core.analyses.views import CheckedView
from aibi.core.engine.readback import readback
from aibi.core.schema.caveats import CORE_SEVERITIES, Caveat, CaveatCode, sort_caveats
from aibi.core.schema.digests import RESULT_MEMBERS, output_digest
from aibi.core.schema.numbers import NotEstimableReason
from aibi.core.schema.output import Data, text
from aibi.core.schema.results import (
    AnalysisRef,
    CohortRef,
    Derivation,
    Disclosure,
    Issuance,
    Readback,
    ResultEnvelope,
    Source,
    Values,
)
from aibi.core.schema.semantics import SEMANTICS_VERSION


def _reasons(value: JsonValue) -> set[str]:
    found: set[str] = set()
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, dict):
            marks = current.get("not_estimable")
            if isinstance(marks, dict):
                found.update(str(reason) for reason in marks.values())
            pending.extend(member for key, member in current.items() if key != "not_estimable")
    return found


def document_of(view: CheckedView) -> dict[str, JsonValue]:
    """The derivation's ``document``: the canonical view, and the object each of its cohorts'
    ids hashes, by id (D318)."""
    return {
        "view": view.identity.view(),
        "cohorts": {cohort.id: cohort.identity.hashed() for cohort in view.cohorts},
    }


def envelope(
    view: CheckedView,
    outcome: Outcome,
    *,
    issuance: str,
    written: Mapping[str, JsonValue],
    params: Mapping[str, JsonValue],
    engine: str,
) -> ResultEnvelope:
    """The result envelope of a view (module docstring), naming the issuance that records it."""
    uses_reference = view.analysis.entry.fields.uses_reference
    cohorts = [
        CohortRef(
            position=position,
            id=cohort.id,
            reference=uses_reference and position == view.reference,
        )
        for position, cohort in enumerate(view.cohorts)
    ]
    values = Values(
        positions=[
            cast(dict[str, JsonValue], position.model_dump(mode="json"))
            for position in outcome.values.positions
        ],
        view=cast(dict[str, JsonValue], outcome.values.view.model_dump(mode="json")),
    )
    dumped_values = cast(JsonValue, values.model_dump(mode="json"))
    caveats = [*outcome.caveats, *view.static_caveats()]
    if _reasons(dumped_values) - {NotEstimableReason.SUPPRESSED.value}:
        caveats.append(
            Caveat(
                code=CaveatCode.NOT_ESTIMABLE,
                severity=CORE_SEVERITIES[CaveatCode.NOT_ESTIMABLE],
                message=[
                    text(
                        "Some values could not be estimated; each null value's reason is in "
                        "the not_estimable map beside it"
                    )
                ],
                affects=["/values"],
            )
        )
    caveats = sort_caveats(caveats)
    dumped: dict[str, JsonValue] = {
        "cohorts": [cast(JsonValue, cohort.model_dump(mode="json")) for cohort in cohorts],
        "population": [
            cast(JsonValue, count.model_dump(mode="json")) for count in outcome.population
        ],
        "analysed": [cast(JsonValue, count.model_dump(mode="json")) for count in outcome.analysed],
        "values": dumped_values,
    }
    leaves: dict[str, list[str]] = {}
    for part in (*view.cohorts, *view.predicates):
        for at, keys in part.leaves.items():
            leaves[at] = sorted({*leaves.get(at, []), *keys})
    labels = [cohort.name for cohort in view.cohorts]
    return ResultEnvelope(
        derivation=Derivation(
            id=view.identity.id,
            document=document_of(view),
            analysis=AnalysisRef(id=view.analysis.id, version=view.analysis.entry.version),
            releases=[view.release],
            packs=dict(view.packs),
            semantics_version=SEMANTICS_VERSION,
            disclosure=Disclosure(min_cell_count=view.disclosure),
            engine=engine,
        ),
        issuance=Issuance(id=issuance, cache_hit=False, values_from=issuance),
        source=Source(document=dict(written), leaves=leaves, params=dict(params)),
        digest=output_digest(dumped, caveats, RESULT_MEMBERS),
        cohorts=cohorts,
        population=list(outcome.population),
        analysed=list(outcome.analysed),
        values=values,
        caveats=caveats,
        readback=Readback(
            cohorts=[readback(cohort) for cohort in view.cohorts], view=view.readback()
        ),
        labels=[Data(data=label) for label in labels],
        charts=[existence_chart(outcome.values, labels)],
    )


def issued_packs(view: CheckedView) -> dict[str, JsonValue]:
    """The packs an issuance of the view records: each with its version and results version."""
    return {pack: version.model_dump(mode="json") for pack, version in view.packs.items()}


__all__ = ["document_of", "envelope", "issued_packs"]

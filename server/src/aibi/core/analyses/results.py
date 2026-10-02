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

The two are apart (D374): ``digested`` gives a view's digested content (``Digested``), which its
result id determines and which ``Digested.content`` writes and ``Digested.read`` reads back as
it was (every number as carried, and the values checked by the analysis's values model, whose
typed values its charts read); ``render`` gives the envelope of digested content for one
issuance, which ``envelope`` does for a view run now.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from pydantic import JsonValue

from aibi.core.analyses import columns, cox, distribution, existence, members, packs, survival
from aibi.core.analyses.charts import (
    columns_charts,
    cox_chart,
    distribution_charts,
    existence_chart,
    survival_chart,
)
from aibi.core.analyses.registry import CORE
from aibi.core.analyses.views import CheckedView
from aibi.core.engine.readback import readback
from aibi.core.schema.analyses import (
    ColumnsValues,
    CoxValues,
    DistributionValues,
    ExistenceValues,
    SurvivalValues,
)
from aibi.core.schema.caveats import CORE_SEVERITIES, Caveat, CaveatCode, sort_caveats
from aibi.core.schema.digests import RESULT_MEMBERS, hashed, output_digest
from aibi.core.schema.ids import DerivationId, Sha256
from aibi.core.schema.numbers import NotEstimableReason
from aibi.core.schema.output import Data, Output, text
from aibi.core.schema.results import (
    Analysed,
    AnalysisRef,
    CohortRef,
    Derivation,
    Disclosure,
    Issuance,
    Population,
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


Outcome = (
    existence.Outcome
    | distribution.Outcome
    | members.Outcome
    | columns.Outcome
    | survival.Outcome
    | cox.Outcome
    | packs.Outcome
)
"""An analysis's digested parts and caveats, as the core's analyses and packs' give them."""


def charts(values: Output | None, labels: list[str]) -> list[dict[str, JsonValue]]:
    """The charts of an analysis's values, as its values model gives them (``charts``);
    ``summary.members`` has none, since a list of keys holds no number to draw (D331), nor a
    pack's analysis (``None``), whose visual output is a render specification among its values
    (§10.1, D343)."""
    if isinstance(values, ExistenceValues):
        return [existence_chart(values, labels)]
    if isinstance(values, DistributionValues):
        return distribution_charts(values, labels)
    if isinstance(values, ColumnsValues):
        return columns_charts(values, labels)
    if isinstance(values, SurvivalValues):
        return [survival_chart(values, labels)]
    if isinstance(values, CoxValues):
        return [cox_chart(values, labels)]
    return []


class _Written(Output):
    """The digested content as ``Digested.content`` writes it: the result it is of and its
    digest beside it, which ``Digested.read`` checks."""

    result: DerivationId
    digest: Sha256
    cohorts: list[CohortRef]
    population: list[Population]
    analysed: list[Analysed]
    values: dict[str, JsonValue]
    caveats: list[Caveat]


class _Content(_Written):
    """The written content with the hash of the rest (``hashed``), which ``Digested.read``
    checks too: the digest rounds numbers and reduces caveats (§7.6, D283), so only the hash
    sees a change below its resolution (D374)."""

    hashed: Sha256


def _hash_of(written: _Written) -> str:
    """The hash of written content, as ``_Content.hashed`` holds it."""
    return "sha256:" + hashed(cast(JsonValue, written.model_dump(mode="json", exclude={"hashed"})))


def _generic(values: Output) -> Values:
    """An analysis's values as an envelope carries them: its positions and its view, dumped."""
    dumped = cast(dict[str, JsonValue], values.model_dump(mode="json"))
    return Values(
        positions=cast(list[dict[str, JsonValue]], dumped["positions"]),
        view=cast(dict[str, JsonValue], dumped["view"]),
    )


def _cohort_refs(view: CheckedView) -> list[CohortRef]:
    """A view's cohorts as its result names them: in view order, the reference marked for an
    analysis that uses one."""
    uses_reference = view.analysis.entry.fields.uses_reference
    return [
        CohortRef(
            position=position,
            id=cohort.id,
            reference=uses_reference and position == view.reference,
        )
        for position, cohort in enumerate(view.cohorts)
    ]


@dataclass(frozen=True)
class Digested:
    """A view's digested content (§7.6, §8.1; D374): the result it is of, its cohorts, their
    population, the units analysed, the values as the analysis left them, and the caveats as
    the envelope carries them (the analysis's, the view's static caveats and ``NOT_ESTIMABLE``),
    sorted, with their digest, which it takes itself. It is what the result's id determines;
    ``typed`` is the analysis's values in its values model, for its charts (``None`` for a
    pack's analysis, whose values are its own), and must give ``values``."""

    result: str
    cohorts: tuple[CohortRef, ...]
    population: tuple[Population, ...]
    analysed: tuple[Analysed, ...]
    values: Values
    typed: Output | None
    caveats: tuple[Caveat, ...]
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            self.typed is not None
            and _generic(self.typed).model_dump_json() != self.values.model_dump_json()
        ):
            raise ValueError("A result's typed values give its values (D374)")
        ordered = tuple(sort_caveats(list(self.caveats)))
        dumped: dict[str, JsonValue] = {
            "cohorts": [cast(JsonValue, c.model_dump(mode="json")) for c in self.cohorts],
            "population": [cast(JsonValue, c.model_dump(mode="json")) for c in self.population],
            "analysed": [cast(JsonValue, c.model_dump(mode="json")) for c in self.analysed],
            "values": cast(JsonValue, self.values.model_dump(mode="json")),
        }
        object.__setattr__(self, "caveats", ordered)
        object.__setattr__(self, "digest", output_digest(dumped, ordered, RESULT_MEMBERS))

    def content(self) -> bytes:
        """The digested content as JSON text, every number as carried, with the result it is of
        and its digest, which ``read`` reads back as it was."""
        written = _Written(
            result=self.result,
            digest=self.digest,
            cohorts=list(self.cohorts),
            population=list(self.population),
            analysed=list(self.analysed),
            values=cast(dict[str, JsonValue], self.values.model_dump(mode="json")),
            caveats=list(self.caveats),
        )
        content = _Content(**dict(written), hashed=_hash_of(written))
        return content.model_dump_json().encode()

    @classmethod
    def read(cls, view: CheckedView, content: bytes) -> "Digested":
        """The digested content ``content`` wrote for ``view``'s result: refused
        (``ValueError``) unless it is as it was written (its hash), is of that result, names
        its cohorts as the view does and gives, taken again, the digest it was written with;
        its values are checked by the analysis's values model, which gives the typed values
        its charts read. A pack's analysis has no values model of the core's, so its content
        is not read (and a pack's result is never given from the cache, M3.5c)."""
        found = _Content.model_validate_json(content)
        if _hash_of(found) != found.hashed:
            raise ValueError("Digested content is read back only as it was written (D374)")
        core = CORE.get(view.analysis.id)
        if core is None or view.analysis.pack is not None:
            raise ValueError("Only a core analysis's digested content is read back (D374)")
        if found.result != view.identity.id or found.cohorts != _cohort_refs(view):
            raise ValueError("Digested content is read back only for the result it is of (D374)")
        typed = core.values.model_validate_json(_json_text(found.values))
        back = cls(
            result=found.result,
            cohorts=tuple(found.cohorts),
            population=tuple(found.population),
            analysed=tuple(found.analysed),
            values=_generic(typed),
            typed=typed,
            caveats=tuple(found.caveats),
        )
        if back.digest != found.digest:
            raise ValueError("Digested content gives the digest it was written with (D374)")
        return back


def _json_text(value: JsonValue) -> str:
    """A JSON value as JSON text, as the values models read it."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def digested(view: CheckedView, outcome: Outcome) -> Digested:
    """The digested content of a view run now (module docstring)."""
    typed = None if isinstance(outcome, packs.Outcome) else outcome.values
    values = outcome.values if isinstance(outcome, packs.Outcome) else _generic(outcome.values)
    caveats = [*outcome.caveats, *view.static_caveats()]
    if _reasons(cast(JsonValue, values.model_dump(mode="json"))) - {
        NotEstimableReason.SUPPRESSED.value
    }:
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
    return Digested(
        result=view.identity.id,
        cohorts=tuple(_cohort_refs(view)),
        population=tuple(outcome.population),
        analysed=tuple(outcome.analysed),
        values=values,
        typed=typed,
        caveats=tuple(caveats),
    )


def render(
    view: CheckedView,
    content: Digested,
    *,
    issuance: str,
    values_from: str,
    written: Mapping[str, JsonValue],
    params: Mapping[str, JsonValue],
    engine: str,
) -> ResultEnvelope:
    """The result envelope of a view's digested content for one issuance (module docstring):
    ``values_from`` names the issuance whose queries gave the content, this one unless it was
    read from the result cache (§8.1)."""
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
        issuance=Issuance(id=issuance, cache_hit=values_from != issuance, values_from=values_from),
        source=Source(document=dict(written), leaves=leaves, params=dict(params)),
        digest=content.digest,
        cohorts=list(content.cohorts),
        population=list(content.population),
        analysed=list(content.analysed),
        values=content.values,
        caveats=list(content.caveats),
        readback=Readback(
            cohorts=[readback(cohort) for cohort in view.cohorts], view=view.readback()
        ),
        labels=[Data(data=label) for label in labels],
        charts=charts(content.typed, labels),
    )


def envelope(
    view: CheckedView,
    outcome: Outcome,
    *,
    issuance: str,
    written: Mapping[str, JsonValue],
    params: Mapping[str, JsonValue],
    engine: str,
) -> ResultEnvelope:
    """The result envelope of a view run now (module docstring), naming the issuance that
    records it."""
    return render(
        view,
        digested(view, outcome),
        issuance=issuance,
        values_from=issuance,
        written=written,
        params=params,
        engine=engine,
    )


def issued_packs(view: CheckedView) -> dict[str, JsonValue]:
    """The packs an issuance of the view records: each with its version and results version."""
    return {pack: version.model_dump(mode="json") for pack, version in view.packs.items()}


__all__ = [
    "Digested",
    "Outcome",
    "charts",
    "digested",
    "document_of",
    "envelope",
    "issued_packs",
    "render",
]

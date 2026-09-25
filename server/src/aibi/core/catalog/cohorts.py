"""The service functions of the query tools of M2: ``validate_document``, ``count_cohort`` and
``explain`` (SPEC §7.6, §7.7, §8.1, §8.4, §8.6, §11.1, §12.2; D299–D303).

Each reads the catalogue's store (``Catalog``) and changes no release; ``count_cohort`` records an
issuance in the derivation log. None of them is an operator operation (§11.2).

**Loading and releases.** A request's ``document`` is loaded as a document as written (§7.1):
parsed again from its JSON text under a document's limits, its parameters substituted and its
schema and checks applied, so that its refusals point into it (D299). With a ``format``,
``validate_document`` first has the format's pack translate it (D303). Each dataset reference of
the document (``dataset``, a cohort's ``dataset``) is resolved to one release, as the catalogue
resolves a pin: unknown datasets, labels and manifests are refused where the reference is
written, listing what is published, and so are withdrawn releases and discarded drafts. The
releases are pinned (§12.2) from before they are read until the tool has answered, the log's
issuances included, and read in outline: descriptors, never rows (§12.2). A draft's effective
*k* is at least its latest published release's, so that a session cannot lower it before it
publishes (D275, D300).

**validate_document** canonicalises the cohorts and returns every refusal of the document
(§8.6), each cohort that canonicalised with its ids as the derivation log knows them (*not
issued* until ``count_cohort`` issues them), its canonical form, readback and the caveats of its
count that need no data (``counts.static_caveats``), and the views unchecked (D284, D299). It
reads no row.

**count_cohort** refuses with the first refusal, as every tool but ``validate_document`` does
(§8.6). It compiles every cohort, runs the document's queries in one query worker
(``queries.run_cohorts``, §14) that ends ``RECORD_SECONDS`` before the call's deadline
(``service.DEADLINE``, D301), a run the deadline stopped (``CallerDeadline``) being refused as
the call's (``tool_seconds``) and one a worker's limit stopped naming that limit, and makes
each cohort's count: its digested parts (``counts.count_parts``), the disclosure pass under its
effective *k* (``suppression``, §8.4), the digest of what the pass left and its readback. It
then records one issuance per cohort, all in one transaction, each naming the call's request
(the document as written and the parameters used), which the log stores once, and the cohort's
SQL as run with its parameters (§12.2, D300); none is recorded unless ``ANSWER_SECONDS`` of the
deadline are left when the transaction would commit, and a withdrawal meanwhile, an erasure of
a dataset the document names recorded since before it was canonicalised (``RELEASE_WITHDRAWN``,
D290), or a log at ``log_bytes``, refuses the whole call. The issuances' ids are drawn as they
are recorded, so the counts that name them are made after. Counts are not cached in M2: each is
a new issuance whose values are its own.

**explain** gives what the derivation log holds for a derivation or an issuance id, and what the
id resolves to (§7.6, D289, D302): never when a derivation was recorded, nor an issuance's
request, which may hold another client's cohorts, notes and names; the operator router serves
those. It reads no row, and works as long as the log holds the id, whatever caches were cleared.

Refusals are raised as ``ToolRefused``; faults of the compiler or the engine are raised as they
are, and the tool call answers them ``INTERNAL_ERROR`` (``mcp.calls``).
"""

import copy
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from pydantic import JsonValue

import aibi
from aibi.core.catalog.service import ALTERNATIVES, DEADLINE, Catalog, Deadline, ToolRefused
from aibi.core.engine.canonical import CanonicalCohort, Canonicalisation, canonicalise
from aibi.core.engine.counts import CountParts, count_parts, static_caveats
from aibi.core.engine.data import Release
from aibi.core.engine.queries import run_cohorts
from aibi.core.engine.readback import readback
from aibi.core.engine.resolve import Label, pack_failed
from aibi.core.engine.sql import Accounting
from aibi.core.engine.suppression import disclosed
from aibi.core.engine.worker import CallerDeadline, QueryRefused
from aibi.core.schema.cohorts import (
    CohortCheck,
    CohortCounts,
    CountCohort,
    DerivationRecord,
    DocumentValidation,
    Explain,
    Explanation,
    IssuanceRecord,
    NamedCount,
    Parameters,
    Translation,
    TranslationNote,
    UncheckedView,
    ValidateDocument,
)
from aibi.core.schema.document import Document
from aibi.core.schema.ids import JSON_POINTER_RE
from aibi.core.schema.jsonio import JsonError, json_value, pointer, utf16_key
from aibi.core.schema.limits import LOG_BYTES, MAX_REFUSALS, MAX_SUMMARY_SEGMENTS, TOOL_SECONDS
from aibi.core.schema.loading import DocumentResult, as_written, load_document
from aibi.core.schema.output import Data, DataSegment, Segment, TextSegment, data, text
from aibi.core.schema.pack_api import TranslationNote as PackNote
from aibi.core.schema.pack_api import UnknownPack
from aibi.core.schema.params import Position
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode, finish_refusals
from aibi.core.schema.results import CohortCount, Disclosure, Issuance, ReleaseRef
from aibi.core.store.blobs import MissingBlobError
from aibi.core.store.derivations import (
    ErasedMeanwhileError,
    Issue,
    LogFullError,
    NotAdmittedError,
    WithdrawnReleaseError,
)
from aibi.core.store.store import Pin, StoreRefused

ENGINE = f"aibi {aibi.__version__}"
"""The engine an output and an issuance name (§8.1, §12.2)."""
NEGATION_MESSAGE = (
    "Under three-valued logic a negation (not, negate, !=, every) keeps a unit whose answer is "
    "unknown out of both a question and its negation, where a format that reads a negation as "
    "the rest of a base counts it in the negation: check that this negation means what the "
    "source meant (§6.3)"
)
"""The core's note on every negation of a translated document (§7.1, D303)."""


def _by_name(cohorts: Mapping[str, CanonicalCohort]) -> list[CanonicalCohort]:
    """Cohorts in the order of their names as UTF-16 code units: JSON object key order is never
    used for anything (§7.4)."""
    return [cohorts[name] for name in sorted(cohorts, key=utf16_key)]


def _views(document: Document | None) -> list[UncheckedView]:
    return [
        UncheckedView(position=index, analysis=Data(data=view.analysis), status="unchecked")
        for index, view in enumerate((document.views or []) if document is not None else [])
    ]


def _parameters(loaded: DocumentResult) -> Parameters:
    return Parameters(
        used=dict(loaded.params_used),
        unused=[Data(data=name) for name in loaded.params_unused],
    )


# --- Releases --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Releases:
    """The releases a document's dataset references name, pinned and in outline."""

    releases: dict[str, Release] = field(default_factory=dict[str, Release])
    """By reference as written."""
    labels: dict[str, Label] = field(default_factory=dict[str, Label])
    """By manifest hash."""
    floors: dict[str, int | None] = field(default_factory=dict[str, int | None])
    """A draft's floor of its own, by manifest hash (D275, D300)."""
    refused: set[str] = field(default_factory=set[str])
    """The paths at which a reference was refused."""
    refusals: list[Refusal] = field(default_factory=list[Refusal])


def _references(document: Document) -> dict[str, list[Position]]:
    """Each dataset reference of the document, and the positions it is written at."""
    found: dict[str, list[Position]] = {}
    if document.dataset is not None:
        found.setdefault(document.dataset, []).append(("dataset",))
    for name, cohort in document.cohorts.items():
        if cohort.dataset is not None:
            found.setdefault(cohort.dataset, []).append(("cohorts", name, "dataset"))
    return found


def _pin_of(reference: str) -> tuple[str, int | str | None]:
    dataset, _, given = reference.partition("@")
    if not given:
        return dataset, None
    if given == "draft" or given.startswith("sha256:"):
        return dataset, given
    return dataset, int(given)


def _releases(
    catalog: Catalog, pin: Pin, document: Document, positions: Mapping[Position, str]
) -> _Releases:
    found = _Releases()
    for reference, places in sorted(_references(document).items()):
        refusal = _release(catalog, pin, reference, found)
        if refusal is None:
            continue
        for place in places:
            at, parameter = as_written(place, positions)
            message = list(refusal.message)
            if parameter is not None:
                message += [text(" (in the value of parameter "), data(parameter), text(")")]
            path = pointer(list(at))
            found.refused.add(path)
            found.refusals.append(refusal.model_copy(update={"path": path, "message": message}))
    return found


def _release(catalog: Catalog, pin: Pin, reference: str, found: _Releases) -> Refusal | None:
    """Resolve and pin one reference into ``found``; its refusal, with no path, if it names no
    release a document may read (§7.1)."""
    store = catalog.store
    dataset, given = _pin_of(reference)
    if not store.labels(dataset):
        available = [d for d in store.datasets() if store.latest(d) is not None]
        return Refusal(
            code=RefusalCode.UNKNOWN_DATASET,
            path=None,
            message=[text("No release of dataset "), data(dataset), text(" was published")],
            alternatives=_listed(available),
        )
    labels = [str(label.label) for label in store.labels(dataset) if not label.withdrawn]
    try:
        resolution = store.resolve(dataset, given)
    except StoreRefused as refused:
        return refused.refusal.model_copy(update={"alternatives": _listed(labels)})
    if resolution.status == "withdrawn":
        return _withdrawn(labels)
    if resolution.status == "discarded" or resolution.label is None:
        return Refusal(
            code=RefusalCode.UNKNOWN_RELEASE,
            path=None,
            message=[text("That draft state was discarded")],
            alternatives=_listed(labels),
        )
    try:
        pin.manifest(resolution.manifest)
    except MissingBlobError:
        return _withdrawn(labels)
    found.releases[reference] = store.outline(resolution.manifest)
    found.labels[resolution.manifest] = resolution.label
    if resolution.label == "draft":
        found.floors[resolution.manifest] = catalog.published_k(pin, dataset)
    return None


def _withdrawn(labels: Sequence[str]) -> Refusal:
    return Refusal(
        code=RefusalCode.RELEASE_WITHDRAWN,
        path=None,
        message=[text("The release was withdrawn: its data and descriptors are not kept")],
        alternatives=_listed(labels),
    )


def _listed(names: Sequence[str]) -> list[Segment]:
    shown: list[Segment] = [data(name) for name in names[:ALTERNATIVES]]
    if len(names) > ALTERNATIVES:
        shown.append(text(f"and {len(names) - ALTERNATIVES} more"))
    return shown


# --- Loading and canonicalising --------------------------------------------------------------


@dataclass(frozen=True)
class _Canonical:
    loaded: DocumentResult
    canonical: Canonicalisation | None
    """``None`` when the document did not load."""
    refusals: list[Refusal]


def _canonical(catalog: Catalog, pin: Pin, written: Mapping[str, JsonValue]) -> _Canonical:
    """The document as written, loaded, its releases resolved and pinned, and canonicalised."""
    loaded = load_document(json.dumps(written, ensure_ascii=False, allow_nan=False))
    document = loaded.document
    if document is None:
        return _Canonical(loaded, None, list(loaded.refusals))
    releases = _releases(catalog, pin, document, loaded.positions)
    canonical = canonicalise(
        document,
        releases.releases,
        labels=releases.labels,
        registry=catalog.registry,
        floor=catalog.floor,
        floors=releases.floors,
        positions=loaded.positions,
    )
    # A reference refused here is refused again, as unknown, by resolution: once is enough.
    kept = [
        refusal
        for refusal in canonical.refusals
        if not (refusal.code == RefusalCode.UNKNOWN_DATASET and refusal.path in releases.refused)
    ]
    return _Canonical(loaded, canonical, finish_refusals([*releases.refusals, *kept]))


# --- Translation -----------------------------------------------------------------------------


def _formats(catalog: Catalog) -> list[str]:
    registry = catalog.registry
    if registry is None:
        return []
    return sorted(name for pack_id in registry.ids for name in registry.pack(pack_id).translators)


def _failed(message: str) -> Refusal:
    return Refusal(code=RefusalCode.PACK_FAILED, path=None, message=[text(message)])


def _translated(
    catalog: Catalog, given: Mapping[str, JsonValue], format: str
) -> tuple[dict[str, JsonValue], list[TranslationNote]] | Refusal:
    """The aibi document the format's translator makes of ``given``, and its notes and the
    core's (D303); or the refusal of a format no pack translates, or of a translation the core
    refuses."""
    registry = catalog.registry
    try:
        translator = None if registry is None else registry.translator(format)
    except UnknownPack:
        translator = None
    if registry is None or translator is None:
        return Refusal(
            code=RefusalCode.NOT_SUPPORTED,
            path=None,
            message=[text("No installed pack translates documents of format "), data(format)],
            alternatives=_listed(_formats(catalog)),
        )
    try:
        made = cast(object, translator.translate(copy.deepcopy(dict(given))))
    except MemoryError:
        raise
    except Exception as error:
        pack_failed(format.partition(".")[0], "translator", error)
        return _failed("The pack's translator of this format failed")
    if not isinstance(made, tuple) or len(cast(tuple[object, ...], made)) != 2:
        return _failed("The pack's translator of this format gave no document and notes")
    translated, notes = cast(tuple[object, object], made)
    try:
        document = json_value(
            dict(cast(Mapping[str, object], translated))
            if isinstance(translated, Mapping)
            else None
        )
    except JsonError:
        document = None
    found = _notes(notes)
    if found is None or not isinstance(document, dict):
        return _failed(
            "The pack's translator of this format gave what is not a JSON document and a list "
            "of notes the core can read"
        )
    found += _negations(document)
    return document, found


def _notes(given: object) -> list[TranslationNote] | None:
    """A translator's notes, each a pointer into the document given and at most
    ``MAX_SUMMARY_SEGMENTS`` segments; at most ``MAX_REFUSALS`` of them. ``None`` otherwise."""
    if not isinstance(given, list | tuple):
        return None
    notes = cast(Sequence[object], given)
    if len(notes) > MAX_REFUSALS:
        return None
    found: list[TranslationNote] = []
    for note in notes:
        if not isinstance(note, PackNote):
            return None
        at, message = cast(object, note.pointer), cast(object, note.message)
        if not isinstance(at, str) or not isinstance(message, list | tuple):
            return None
        segments = list(cast(Sequence[object], message))
        if JSON_POINTER_RE.fullmatch(at) is None or len(segments) > MAX_SUMMARY_SEGMENTS:
            return None
        if not all(isinstance(segment, TextSegment | DataSegment) for segment in segments):
            return None
        found.append(
            TranslationNote(
                pointer=at,
                translated=False,
                message=cast(list[Segment], segments),
            )
        )
    return found


def _negations(document: Mapping[str, JsonValue]) -> list[TranslationNote]:
    """The core's note on every negation in the translated document's cohorts (§7.1, D303):
    each ``not`` clause, each ``negate: true`` and ``op: "!="`` of a leaf, and each ``every``
    quantifier, which asks that no child fail (§6.4). Each keeps a unit whose answer is unknown
    out of both a question and its negation. Pack leaves are read as they are written, so that
    one whose members negate is flagged too; the notes are sorted by pointer."""
    found: list[TranslationNote] = []
    cohorts = document.get("cohorts")
    if not isinstance(cohorts, dict):
        return found
    pending: list[tuple[JsonValue, list[str | int]]] = [
        (cohort, ["cohorts", name]) for name, cohort in sorted(cohorts.items())
    ]
    while pending:
        node, at = pending.pop()
        if isinstance(node, list):
            pending.extend((item, [*at, index]) for index, item in enumerate(node))
        elif isinstance(node, dict):
            found += [_negation([*at, *member]) for member in _negating(node)]
            pending.extend((member, [*at, key]) for key, member in node.items())
    return sorted(found, key=lambda note: utf16_key(note.pointer))


def _negating(node: Mapping[str, JsonValue]) -> list[list[str | int]]:
    """The members of one JSON object that negate, as pointer tokens below it."""
    found: list[list[str | int]] = []
    if "not" in node:
        found.append(["not"])
    if node.get("negate") is True:
        found.append(["negate"])
    if node.get("op") == "!=":
        found.append(["op"])
    quantifier = node.get("quantifier")
    if quantifier == "every":
        found.append(["quantifier"])
    elif isinstance(quantifier, list):
        found += [["quantifier", index] for index, item in enumerate(quantifier) if item == "every"]
    return found


def _negation(at: list[str | int]) -> TranslationNote:
    return TranslationNote(pointer=pointer(at), translated=True, message=[text(NEGATION_MESSAGE)])


# --- validate_document -----------------------------------------------------------------------


def validate_document(catalog: Catalog, request: ValidateDocument) -> DocumentValidation:
    """Every refusal of a document, and its cohorts' canonical forms, ids, readbacks and static
    caveats, reading no row (module docstring)."""
    written: Mapping[str, JsonValue] = request.document
    translation: Translation | None = None
    if request.format is not None:
        translated = _translated(catalog, request.document, request.format)
        if isinstance(translated, Refusal):
            return DocumentValidation(valid=False, refusals=[translated], cohorts=[], views=[])
        written, notes = translated
        translation = Translation(format=request.format, document=dict(written), notes=notes)
    with catalog.store.pin() as pin:
        found = _canonical(catalog, pin, written)
        cohorts = [] if found.canonical is None else _by_name(found.canonical.cohorts)
        checks = [_check(catalog, cohort) for cohort in cohorts]
    loaded = found.loaded
    return DocumentValidation(
        valid=not found.refusals,
        refusals=found.refusals,
        translation=translation,
        cohorts=checks,
        views=_views(loaded.document),
        params=None if loaded.document is None else _parameters(loaded),
    )


def _check(catalog: Catalog, cohort: CanonicalCohort) -> CohortCheck:
    status = catalog.store.explain(cohort.id).status
    if status == "unknown":
        raise RuntimeError("a derivation id is never an unknown issuance id")
    return CohortCheck(
        cohort=Data(data=cohort.name),
        id=cohort.id,
        computation_id=cohort.computation_id,
        status=status,
        form=dict(cohort.form),
        unit=cohort.resolved.unit,
        release=cohort.release,
        disclosure=Disclosure(min_cell_count=cohort.identity.disclosure),
        packs=dict(cohort.packs),
        leaves={at: list(keys) for at, keys in cohort.leaves.items()},
        readback=readback(cohort),
        caveats=static_caveats(cohort),
    )


# --- count_cohort ----------------------------------------------------------------------------

RECORD_SECONDS = 2.0
"""Of a call's deadline, the seconds its queries leave for recording its issuances and answering
(D300)."""
ANSWER_SECONDS = 1.0
"""Of a call's deadline, the seconds kept, once its issuances are recorded, for the commit, the
release of its pins and the answer: a call records none unless this much is left (D300)."""


def _late(deadline: Deadline) -> ToolRefused:
    seconds = max(1, round(deadline.seconds))
    return ToolRefused(
        [
            Refusal(
                code=RefusalCode.LIMIT_EXCEEDED,
                path=None,
                message=[text(f"The call did not end within {seconds} seconds")],
                limit=Limit(name=TOOL_SECONDS, max=seconds),
            )
        ]
    )


def count_cohort(catalog: Catalog, request: CountCohort) -> CohortCounts:
    """Each cohort of a document counted, disclosed and issued (module docstring)."""
    workers = catalog.workers
    if workers is None:
        raise ToolRefused(
            [
                Refusal(
                    code=RefusalCode.NOT_SUPPORTED,
                    path=None,
                    message=[text("This server runs no query workers, so it counts nothing")],
                )
            ]
        )
    deadline = DEADLINE.get()
    store = catalog.store
    erasures = store.derivations.erasure_mark()
    with store.pin() as pin:
        found = _canonical(catalog, pin, request.document)
        if found.refusals or found.canonical is None:
            raise ToolRefused(found.refusals[:1])
        cohorts = _by_name(found.canonical.cohorts)
        manifests = sorted({cohort.release.manifest for cohort in cohorts})
        sources = {manifest: store.sources(manifest) for manifest in manifests}
        ends = None if deadline is None else deadline.at - RECORD_SECONDS
        if ends is not None and time.monotonic() >= ends:
            raise _late(cast(Deadline, deadline))
        try:
            runs = run_cohorts([cohort.resolved for cohort in cohorts], sources, workers, ends=ends)
        except CallerDeadline:
            raise _late(cast(Deadline, deadline)) from None
        except QueryRefused as refused:
            raise ToolRefused([refused.refusal]) from None
        written = dict(request.document)
        params = dict(found.loaded.params_used)
        counted = [
            _counted(cohort, run.accounting, run.sql, run.parameters, written, params)
            for cohort, run in zip(cohorts, runs, strict=True)
        ]

        def admit() -> bool:
            return deadline is None or time.monotonic() < deadline.at - ANSWER_SECONDS

        try:
            issued = store.derivations.issue_all(
                [issue for _, issue in counted], admit=admit, erasures_after=erasures
            )
        except WithdrawnReleaseError:
            raise ToolRefused([_withdrawn([])]) from None
        except ErasedMeanwhileError:
            raise ToolRefused([_erased_meanwhile()]) from None
        except LogFullError as full:
            raise ToolRefused([_full(full.log_bytes)]) from None
        except NotAdmittedError:
            raise _late(cast(Deadline, deadline)) from None
    return CohortCounts(
        counts=[
            _named(cohort, parts, identifier)
            for cohort, (parts, _), identifier in zip(cohorts, counted, issued, strict=True)
        ],
        views=_views(found.loaded.document),
        params=_parameters(found.loaded),
    )


def _erased_meanwhile() -> Refusal:
    """An erasure of a dataset the document names was recorded while the call ran, so it records
    nothing (D290): its releases' rows may be what the erasure took."""
    return Refusal(
        code=RefusalCode.RELEASE_WITHDRAWN,
        path=None,
        message=[
            text(
                "An erasure of a dataset the document names was recorded while the call ran, so "
                "nothing was counted; count again over the releases left"
            )
        ],
    )


def _full(log_bytes: int) -> Refusal:
    return Refusal(
        code=RefusalCode.LIMIT_EXCEEDED,
        path=None,
        message=[
            text(
                f"The derivation log holds its limit of {log_bytes} bytes, so it records no more "
                "counts until an operator prunes it"
            )
        ],
        limit=Limit(name=LOG_BYTES, max=log_bytes),
    )


def _counted(
    cohort: CanonicalCohort,
    accounting: Accounting,
    sql: Sequence[str],
    parameters: Mapping[str, JsonValue],
    written: dict[str, JsonValue],
    params: dict[str, JsonValue],
) -> tuple[CountParts, Issue]:
    """A cohort's count, disclosed, and the issuance that records it (D300): the call's
    request, which the log stores once for all its cohorts, and the cohort's SQL."""
    parts = disclosed(count_parts(cohort, accounting), cohort.identity.disclosure)
    release = cast(JsonValue, cohort.release.model_dump(mode="json"))
    packs = {pack: version.model_dump(mode="json") for pack, version in cohort.packs.items()}
    issue = Issue(
        derivation=cohort.id,
        kind="cohort",
        hashed=cohort.identity.hashed(),
        releases=[release],
        tool="count_cohort",
        written=written,
        params=params,
        sql={"statements": list(sql), "parameters": dict(parameters)},
        engine=ENGINE,
        packs=cast(JsonValue, packs),
    )
    return parts, issue


def _named(cohort: CanonicalCohort, parts: CountParts, issuance: str) -> NamedCount:
    """A cohort's count as ``count_cohort`` returns it, naming the issuance that recorded it."""
    count = CohortCount(
        id=cohort.id,
        digest=parts.digest,
        population=parts.population,
        size=parts.size,
        disclosure=Disclosure(min_cell_count=cohort.identity.disclosure),
        readback=readback(cohort),
        caveats=list(parts.caveats),
        releases=[cohort.release],
        issuance=Issuance(id=issuance, cache_hit=False, values_from=issuance),
    )
    return NamedCount(
        cohort=Data(data=cohort.name),
        count=count,
        leaves={at: list(keys) for at, keys in cohort.leaves.items()},
    )


# --- explain ---------------------------------------------------------------------------------


def explain(catalog: Catalog, request: Explain) -> Explanation:
    """What the derivation log holds for an id, and what the id resolves to (D302): of a
    derivation, its kind, object and releases, never when it was recorded; of an issuance,
    never its request (the document as written and the parameters), which the operator router
    alone serves."""
    found = catalog.store.explain(request.id)
    derivation = found.derivation
    issuance = found.issuance
    return Explanation(
        id=request.id,
        status=found.status,
        derivation=None
        if derivation is None
        else DerivationRecord(
            id=derivation.id,
            kind=derivation.kind,
            object=None
            if derivation.hashed is None
            else cast(dict[str, JsonValue], derivation.hashed),
            releases=[
                ReleaseRef.model_validate(release)
                for release in cast(list[JsonValue], derivation.releases)
            ],
        ),
        issuance=None
        if issuance is None
        else IssuanceRecord(
            id=issuance.id,
            derivation=issuance.derivation,
            tool=issuance.tool,
            sql=issuance.sql,
            values_from=issuance.values_from,
            engine=issuance.engine,
            packs=cast(dict[str, JsonValue], issuance.packs),
            at=issuance.at,
        ),
    )


__all__ = [
    "ANSWER_SECONDS",
    "ENGINE",
    "NEGATION_MESSAGE",
    "RECORD_SECONDS",
    "count_cohort",
    "explain",
    "validate_document",
]

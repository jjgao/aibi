"""The operator router (SPEC §11.2, D264–D267, D269).

The application mounts it at ``/operator``, behind request protection, and the MCP transport never
does. Request protection checks every operator request's curator token and operator name and
records the attribution in its scope; the router's own dependency (``require_operator``) refuses a
request without it, so the router fails closed wherever it is mounted.

**Reads** are ``GET``: the CSRF token, the datasets, one dataset, its curation queue and one
descriptor of a release or of the draft. They change no release, label, session, proposal or
audit entry; the store's housekeeping when a pin is released may still run, as for any read. A
dataset's state names its open session, never the session's handle. A dataset with no label is
``UNKNOWN_DATASET`` on every route that names one, but an upload and an import, which make it;
the route checks this once it has read the body and taken its place, so that a body refused,
or one place too many, is answered as on any dataset.

**Changes** are ``POST``: uploads, imports and re-imports, withdrawals, erasures, running the
curation proposers, rejecting a proposal, and opening, changing, publishing, discarding and taking
over a session (accepting a proposal is an ``accept`` edit, D248). Each is one synchronous
request whose service call runs in a worker thread, which a dropped connection does not cancel.
A body is a JSON object (``{}`` when empty), received within the deadlines of an upload (below,
its length at most ``max_body_bytes`` when it declares none) and read by ``load_request`` in a
worker thread, so that parsing a large body never holds up the event loop, and never by the
framework; a body refused, or one whose stored text holds a secret's shape (``stored_secrets``),
is answered 422 with its refusals. The service functions raise the core's refusals, which
``api.errors`` answers.

Uploads, imports, re-imports and erasures each take one of ``concurrent_imports`` places, and one
more is refused at once, never queued (``LIMIT_EXCEEDED`` naming ``concurrent_imports``, D266):
each holds a worker thread while it runs, which the reads and the other operations share. An
import's source is an absolute path on the server, confined by a fresh ``Confinement`` of the
upload area and the import directories (D232), or an upload of the same dataset; its limits and
its ``at`` are the server's. An upload declares its length in one ``Content-Length``, without
``Transfer-Encoding`` (``LENGTH_REQUIRED``, 411, otherwise), and streams its body into the
upload area (D234), never holding it in memory; a body that sends nothing for
``upload_idle_seconds`` is refused (``LIMIT_EXCEEDED`` naming ``upload_idle_seconds``, 408), and
so is one that has not ended by its deadline, ``upload_idle_seconds`` and its declared length at
``upload_min_bytes_per_second`` after it began (``LIMIT_EXCEEDED`` naming ``upload_seconds``,
408): either frees its place and its thread. A slow upload that keeps sending is never refused
for its gaps alone, and a trickle cannot hold a place for longer than its deadline.
"""

import math
import threading
from collections.abc import AsyncIterator, Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Annotated

import anyio
import anyio.from_thread
import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import Path as PathParameter
from pydantic import BaseModel
from starlette.responses import Response

from aibi.core.importers.confine import Confinement
from aibi.core.importers.run import Published, import_dataset, reimport_dataset
from aibi.core.importers.uploads import UploadArea
from aibi.core.operator.auth import CSRF_KEY, OPERATOR_KEY, OPERATOR_PREFIX, valid_name
from aibi.core.schema.curation import CurationQueue, QueueNote
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.limits import (
    CONCURRENT_IMPORTS,
    MAX_BODY_BYTES,
    MAX_IDENTIFIER,
    UPLOAD_IDLE_SECONDS,
    UPLOAD_SECONDS,
    ImportLimits,
)
from aibi.core.schema.loading import RequestResult, load_request
from aibi.core.schema.operator import (
    ChangeNote,
    Csrf,
    Datasets,
    DatasetState,
    DescriptorShown,
    DraftChanged,
    Empty,
    ErasedOut,
    EraseRequest,
    ImportPublished,
    ImportRequest,
    LabelState,
    OpenSession,
    PathSource,
    ProposersRan,
    Refusals,
    Rejected,
    SessionChange,
    SessionEnd,
    SessionEnded,
    SessionOpened,
    SessionPublishedOut,
    SkippedProposal,
    Uploaded,
    Withdrawn,
    WithdrawRequest,
    stored_secrets,
)
from aibi.core.schema.output import Output
from aibi.core.schema.pack_api import ConfinedPath, ImportNote, ImportOptions, PackRegistry
from aibi.core.schema.refusals import Limit, RefusalCode
from aibi.core.store import sessions
from aibi.core.store.erasure import erase
from aibi.core.store.proposals import (
    ProposersRun,
    curation_queue,
    reject_proposal,
    run_proposers,
)
from aibi.core.store.sessions import Opened
from aibi.core.store.store import Store, StoreRefused

DATASET_PATTERN = r"^[a-z](?:_?[a-z0-9])*_?$"
"""A dataset id in a path: an identifier, which never holds ``__`` (§5.1)."""
_DESCRIPTOR_CHARACTERS = 4 + MAX_IDENTIFIER + 1 + 16 * (MAX_IDENTIFIER + 1)

DatasetParameter = Annotated[str, PathParameter(pattern=DATASET_PATTERN, max_length=MAX_IDENTIFIER)]
DescriptorParameter = Annotated[str, PathParameter(max_length=_DESCRIPTOR_CHARACTERS)]
ProposalParameter = Annotated[int, PathParameter(ge=1, le=MAX_SAFE_INTEGER)]
ReleaseQuery = Annotated[
    str | None, Query(pattern=r"^(?:[1-9][0-9]{0,15}|draft|sha256:[0-9a-f]{64})$")
]
LabelQuery = Annotated[int | None, Query(ge=1, le=MAX_SAFE_INTEGER)]
ExtensionQuery = Annotated[str, Query(min_length=1, max_length=16)]


@dataclass(frozen=True)
class Services:
    """What the operator router calls: the store, the upload area, the import directories
    (real paths, checked at start) and the server's import limits (D253)."""

    store: Store
    uploads: UploadArea
    import_directories: tuple[Path, ...]
    limits: ImportLimits
    registry: PackRegistry | None = None
    concurrent_imports: int = 2
    """Uploads, imports, re-imports and erasures that run at once (D266)."""
    upload_idle_seconds: float = 60
    """How long an upload, or a JSON body, may send nothing before it is refused (D266)."""
    upload_min_bytes_per_second: int = 32 * 1024
    """The slowest an upload, or a JSON body, may average, after ``upload_idle_seconds``
    (D266)."""
    max_body_bytes: int = MAX_BODY_BYTES
    """The largest JSON body request protection lets through (D260): with the slowest rate, the
    deadline of a body that does not declare its length."""
    token_digest: bytes | None = field(default=None, repr=False)
    """The configured curator token's SHA-256, which stored text may not hold anywhere (D265)."""


def require_operator(request: Request) -> str:
    """The request's attribution, ``operator:<name>``, which request protection recorded; a
    request without one is refused as one without the curator token."""
    found = request.scope.get(OPERATOR_KEY)
    if not isinstance(found, str):
        raise HTTPException(status_code=401)
    kind, _, name = found.partition(":")
    if kind != "operator" or not valid_name(name):
        raise HTTPException(status_code=401)
    return found


Operator = Annotated[str, Depends(require_operator)]


def _json(output: Output) -> Response:
    return Response(output.model_dump_json(), media_type="application/json")


def _loaded[M: BaseModel](source: bytes, model: type[M], digest: bytes | None) -> RequestResult[M]:
    loaded = load_request(source, model)
    if loaded.value is None:
        return loaded
    refused = stored_secrets(loaded.value, digest)
    return RequestResult(None, refused) if refused else loaded


async def _body[M: BaseModel](services: Services, request: Request, model: type[M]) -> M | Response:
    """The body, received within its deadlines (``_Deadlines``) and read as ``model`` in a worker
    thread, or the response that refuses it (422)."""
    length = _declared(request)
    deadlines = _Deadlines.of(services, services.max_body_bytes if length is None else length)
    stream = request.stream()
    source = bytearray()
    try:
        while (chunk := await deadlines.next_chunk(stream)) is not None:
            source += chunk
    finally:
        await stream.aclose()
    loaded = await anyio.to_thread.run_sync(_loaded, bytes(source), model, services.token_digest)
    if loaded.value is None:
        body = Refusals(refusals=loaded.refusals).model_dump_json()
        return Response(body, status_code=422, media_type="application/json")
    return loaded.value


def _note(note: ImportNote) -> QueueNote:
    return QueueNote(
        kind=note.kind,
        subject=note.subject,
        message=list(note.message),
        count=note.count,
        rows=list(note.rows),
    )


def _ran(run: ProposersRun | None) -> ProposersRan | None:
    if run is None:
        return None
    return ProposersRan(
        proposals=list(run.proposals),
        skipped=[
            SkippedProposal(
                pack=skipped.pack,
                descriptor=skipped.descriptor,
                pointer=skipped.pointer,
                codes=list(skipped.codes),
            )
            for skipped in run.skipped
        ],
    )


def _published(dataset: str, published: Published) -> ImportPublished:
    return ImportPublished(
        dataset=dataset,
        label=published.label,
        manifest=published.manifest,
        notes=[_note(note) for note in published.notes],
        changes=[
            ChangeNote(descriptor=c.descriptor, pointer=c.pointer, happened=c.happened)
            for c in published.changes
        ],
        proposers=_ran(published.proposers),
    )


def _opened(dataset: str, opened: Opened) -> SessionOpened:
    return SessionOpened(
        dataset=dataset,
        session=opened.session,
        base=opened.base,
        draft=opened.draft,
        handle=opened.handle,
    )


def _known(store: Store, dataset: str) -> None:
    if not store.labels(dataset):
        raise StoreRefused(RefusalCode.UNKNOWN_DATASET, "No release of the dataset was published")


def _stalled(seconds: float) -> StoreRefused:
    return StoreRefused(
        RefusalCode.LIMIT_EXCEEDED,
        f"The request's body sent nothing for {seconds:g} seconds",
        limit=Limit(name=UPLOAD_IDLE_SECONDS, max=max(1, round(seconds))),
    )


def _overdue(seconds: float) -> StoreRefused:
    return StoreRefused(
        RefusalCode.LIMIT_EXCEEDED,
        f"The request's body did not end within {seconds:g} seconds, the time its length allows "
        "at the slowest rate the server accepts",
        limit=Limit(name=UPLOAD_SECONDS, max=max(1, math.ceil(seconds))),
    )


def _declared(request: Request) -> int | None:
    """The body's length, if it declares one in one ``Content-Length`` and is not framed by
    ``Transfer-Encoding``, which h11 lets override it."""
    if "transfer-encoding" in request.headers:
        return None
    given = request.headers.getlist("content-length")
    return int(given[0]) if len(given) == 1 and given[0].isdigit() else None


@dataclass(frozen=True)
class _Deadlines:
    """A body's deadlines (D266): it is refused if it sends nothing for ``idle`` seconds
    (``upload_idle_seconds``), or has not ended ``allowed`` seconds after it began: ``idle`` and
    its length at the slowest rate the server accepts (``upload_seconds``)."""

    idle: float
    allowed: float
    ends: float

    @classmethod
    def of(cls, services: Services, length: int) -> "_Deadlines":
        idle = services.upload_idle_seconds
        allowed = idle + length / services.upload_min_bytes_per_second
        return cls(idle, allowed, anyio.current_time() + allowed)

    async def next_chunk(self, stream: AsyncIterator[bytes]) -> bytes | None:
        """The body's next chunk, ``None`` at its end, or the refusal of a deadline passed."""
        left = self.ends - anyio.current_time()
        if left <= 0:
            raise _overdue(self.allowed)
        try:
            with anyio.fail_after(min(self.idle, left)):
                return await anext(stream)
        except StopAsyncIteration:
            return None
        except TimeoutError:
            raise (_stalled(self.idle) if self.idle < left else _overdue(self.allowed)) from None


def _state(store: Store, dataset: str) -> DatasetState:
    _known(store, dataset)
    labels = store.labels(dataset)
    latest = store.latest(dataset)
    session = store.db.open_session_of(dataset)
    return DatasetState(
        dataset=dataset,
        latest=None if latest is None else latest.label,
        labels=[
            LabelState(
                label=label.label,
                manifest=label.manifest,
                status="withdrawn" if label.withdrawn else "published",
            )
            for label in labels
        ],
        session=None
        if session is None
        else OpenSession(
            session=session.id, base=session.base, draft=session.draft, opened_by=session.opened_by
        ),
    )


class _Places:
    """The places of the uploads, imports, re-imports and erasures that run at once (D266)."""

    def __init__(self, count: int) -> None:
        self._count = count
        self._free = threading.BoundedSemaphore(count)

    @contextmanager
    def taken(self) -> Generator[None]:
        if not self._free.acquire(blocking=False):
            raise StoreRefused(
                RefusalCode.LIMIT_EXCEEDED,
                f"{self._count} uploads, imports, re-imports or erasures are running; try again "
                "once one ends",
                limit=Limit(name=CONCURRENT_IMPORTS, max=self._count),
            )
        try:
            yield
        finally:
            self._free.release()


def operator_router(services: Services) -> APIRouter:
    """The router, at ``/operator``, every route behind ``require_operator``."""
    router = APIRouter(prefix=OPERATOR_PREFIX, dependencies=[Depends(require_operator)])
    store = services.store
    places = _Places(services.concurrent_imports)

    async def run_known[T](dataset: str, call: Callable[[], T]) -> T:
        def known() -> T:
            _known(store, dataset)
            return call()

        return await run(known)

    async def run[T](call: Callable[[], T]) -> T:
        return await anyio.to_thread.run_sync(call)

    # --- Reads ---------------------------------------------------------------------------

    @router.get("/csrf", response_model=Csrf)
    def csrf(request: Request) -> Response:
        token = request.scope.get(CSRF_KEY)
        if not isinstance(token, str):
            raise HTTPException(status_code=401)
        return _json(Csrf(csrf=token))

    @router.get("/datasets", response_model=Datasets)
    def datasets() -> Response:
        return _json(Datasets(datasets=[_state(store, found) for found in store.datasets()]))

    @router.get("/datasets/{dataset}", response_model=DatasetState)
    def dataset_state(dataset: DatasetParameter) -> Response:
        return _json(_state(store, dataset))

    @router.get("/datasets/{dataset}/queue", response_model=CurationQueue)
    def queue(dataset: DatasetParameter, release: LabelQuery = None) -> Response:
        _known(store, dataset)
        return _json(curation_queue(store, dataset, release=release))

    @router.get("/datasets/{dataset}/descriptors/{descriptor}", response_model=DescriptorShown)
    def descriptor(
        dataset: DatasetParameter, descriptor: DescriptorParameter, release: ReleaseQuery = None
    ) -> Response:
        _known(store, dataset)
        pinned: int | str | None = release
        if release is not None and release.isdigit():
            pinned = int(release)
        resolution = store.resolve(dataset, pinned)
        if resolution.status == "withdrawn":
            raise StoreRefused(
                RefusalCode.RELEASE_WITHDRAWN,
                "The release was withdrawn: its descriptors are not kept",
            )
        if resolution.status == "discarded" or resolution.label is None:
            raise StoreRefused(RefusalCode.UNKNOWN_RELEASE, "That draft state was discarded")
        with store.pin() as pin:
            pin.manifest(resolution.manifest)
            held = store.descriptors(resolution.manifest)
        found = next((item for item in held if item.id == descriptor), None)
        if found is None:
            raise StoreRefused(RefusalCode.NOT_FOUND, "The release holds no descriptor of that id")
        shown = DescriptorShown(
            dataset=dataset,
            release=resolution.manifest,
            label=resolution.label,
            descriptor=found.model_dump(mode="json"),
        )
        return _json(shown)

    # --- Uploads and imports -------------------------------------------------------------

    @router.post("/datasets/{dataset}/uploads", response_model=Uploaded)
    async def upload(
        dataset: DatasetParameter, extension: ExtensionQuery, request: Request
    ) -> Response:
        length = _declared(request)
        if length is None:
            raise StoreRefused(
                RefusalCode.LENGTH_REQUIRED,
                "An upload declares its length in one Content-Length header, without "
                "Transfer-Encoding",
            )
        deadlines = _Deadlines.of(services, length)
        stream = request.stream()
        size = 0

        def chunks() -> Iterator[bytes]:
            nonlocal size
            while (chunk := anyio.from_thread.run(deadlines.next_chunk, stream)) is not None:
                size += len(chunk)
                yield chunk

        def put() -> ConfinedPath:
            with places.taken():
                return services.uploads.put(
                    dataset, chunks(), extension, limit=services.limits.import_bytes
                )

        try:
            stored = await run(put)
        finally:
            await stream.aclose()
        return _json(Uploaded(dataset=dataset, upload=stored.name, bytes=size))

    def source_of(confinement: Confinement, dataset: str, request: ImportRequest) -> ConfinedPath:
        given = request.source
        if isinstance(given, PathSource):
            return confinement.confine(given.path)
        digest, _, extension = given.upload.partition(".")
        return confinement.confine(services.uploads.path(dataset, digest, extension))

    def importing(dataset: str, request: ImportRequest, by: str, reimport: bool) -> Output:
        with places.taken():
            if reimport:
                _known(store, dataset)
            confinement = Confinement.of(services.uploads.root, *services.import_directories)
            source = source_of(confinement, dataset, request)
            options = ImportOptions(
                dataset=dataset,
                reader=confinement,
                limits=services.limits,
                at=store.now(),
                name=request.name,
                original_name=request.original_name,
            )
            operation = reimport_dataset if reimport else import_dataset
            published = operation(
                store, source, options, by, registry=services.registry, pack=request.pack
            )
        return _published(dataset, published)

    @router.post("/datasets/{dataset}/import", response_model=ImportPublished)
    async def import_(dataset: DatasetParameter, request: Request, by: Operator) -> Response:
        body = await _body(services, request, ImportRequest)
        if isinstance(body, Response):
            return body
        return _json(await run(partial(importing, dataset, body, by, False)))

    @router.post("/datasets/{dataset}/reimport", response_model=ImportPublished)
    async def reimport(dataset: DatasetParameter, request: Request, by: Operator) -> Response:
        body = await _body(services, request, ImportRequest)
        if isinstance(body, Response):
            return body
        return _json(await run(partial(importing, dataset, body, by, True)))

    # --- Withdrawal, erasure and proposers -----------------------------------------------

    @router.post("/datasets/{dataset}/withdraw", response_model=Withdrawn)
    async def withdraw(dataset: DatasetParameter, request: Request, by: Operator) -> Response:
        body = await _body(services, request, WithdrawRequest)
        if isinstance(body, Response):
            return body
        labels = await run_known(dataset, partial(store.withdraw, dataset, body.release, by))
        return _json(Withdrawn(dataset=dataset, labels=labels))

    def erasing(dataset: str, request: EraseRequest, by: str) -> ErasedOut:
        with places.taken():
            _known(store, dataset)
            erased = erase(
                store,
                dataset,
                request.table,
                list(request.key),
                by,
                uploads=services.uploads.delete_dataset,
                redact_only=request.redact_only,
            )
        return ErasedOut(
            dataset=dataset,
            withdrawn=list(erased.withdrawn),
            terms=erased.terms,
            redacted=erased.redacted,
            uploads_pending=erased.uploads_pending,
        )

    @router.post("/datasets/{dataset}/erase", response_model=ErasedOut)
    async def erase_(dataset: DatasetParameter, request: Request, by: Operator) -> Response:
        body = await _body(services, request, EraseRequest)
        if isinstance(body, Response):
            return body
        return _json(await run(partial(erasing, dataset, body, by)))

    def proposing(dataset: str) -> ProposersRan:
        if store.latest(dataset) is None:
            raise StoreRefused(RefusalCode.UNKNOWN_RELEASE, "The dataset has no published release")
        if services.registry is None:
            return ProposersRan(proposals=[], skipped=[])
        ran = _ran(run_proposers(store, dataset, services.registry))
        assert ran is not None
        return ran

    @router.post("/datasets/{dataset}/proposers", response_model=ProposersRan)
    async def proposers(dataset: DatasetParameter, request: Request) -> Response:
        body = await _body(services, request, Empty)
        if isinstance(body, Response):
            return body
        return _json(await run_known(dataset, partial(proposing, dataset)))

    @router.post("/datasets/{dataset}/proposals/{proposal}/reject", response_model=Rejected)
    async def reject(
        dataset: DatasetParameter, proposal: ProposalParameter, request: Request, by: Operator
    ) -> Response:
        body = await _body(services, request, Empty)
        if isinstance(body, Response):
            return body
        await run_known(dataset, partial(reject_proposal, store, dataset, proposal, by))
        return _json(Rejected(dataset=dataset, proposal=proposal))

    # --- Sessions ------------------------------------------------------------------------

    @router.post("/datasets/{dataset}/session/open", response_model=SessionOpened)
    async def open_(dataset: DatasetParameter, request: Request, by: Operator) -> Response:
        body = await _body(services, request, Empty)
        if isinstance(body, Response):
            return body
        opened = await run_known(dataset, partial(sessions.open_session, store, dataset, by))
        return _json(_opened(dataset, opened))

    @router.post("/datasets/{dataset}/session/change", response_model=DraftChanged)
    async def change(dataset: DatasetParameter, request: Request, by: Operator) -> Response:
        body = await _body(services, request, SessionChange)
        if isinstance(body, Response):
            return body
        changing = partial(
            sessions.change,
            store,
            dataset,
            body.handle,
            body.expected,
            body,
            by,
            registry=services.registry,
        )
        return _json(DraftChanged(dataset=dataset, draft=await run_known(dataset, changing)))

    @router.post("/datasets/{dataset}/session/publish", response_model=SessionPublishedOut)
    async def publish(dataset: DatasetParameter, request: Request, by: Operator) -> Response:
        body = await _body(services, request, SessionEnd)
        if isinstance(body, Response):
            return body
        publishing = partial(
            sessions.publish,
            store,
            dataset,
            body.handle,
            body.expected,
            by,
            registry=services.registry,
        )
        published = await run_known(dataset, publishing)
        out = SessionPublishedOut(
            dataset=dataset, label=published.label, proposers=_ran(published.proposers)
        )
        return _json(out)

    @router.post("/datasets/{dataset}/session/discard", response_model=SessionEnded)
    async def discard(dataset: DatasetParameter, request: Request, by: Operator) -> Response:
        body = await _body(services, request, SessionEnd)
        if isinstance(body, Response):
            return body
        discarding = partial(sessions.discard, store, dataset, body.handle, body.expected, by)
        await run_known(dataset, discarding)
        return _json(SessionEnded(dataset=dataset, outcome="discarded"))

    @router.post("/datasets/{dataset}/session/take-over", response_model=SessionOpened)
    async def take_over(dataset: DatasetParameter, request: Request, by: Operator) -> Response:
        body = await _body(services, request, Empty)
        if isinstance(body, Response):
            return body
        opened = await run_known(dataset, partial(sessions.take_over, store, dataset, by))
        return _json(_opened(dataset, opened))

    return router


__all__ = ["DATASET_PATTERN", "Operator", "Services", "operator_router", "require_operator"]

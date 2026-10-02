"""The operator router's operations over HTTP (SPEC §11.2, D268), for the operator CLI.

``OperatorClient`` sends each operation to the router with the curator token as a bearer token and
the operator's name in ``Aibi-Operator``, over an ``httpx.Client`` its caller configures (the CLI's
takes no proxy from the environment, follows no redirect and verifies TLS). Dataset and
descriptor ids are percent-encoded into paths; handles and erasure keys travel in bodies only.
Every answer is read with the bounded JSON parser and the output models: a refusal is raised as
``Refused``, with the status and every refusal, and anything that is not the router's answer, a
server that cannot be reached, or an answer that cannot be read (any ``httpx.HTTPError``, a body
that fails to decode included), as ``Unreachable``. Requests ask for no content coding
(``Accept-Encoding: identity``). Reads time out after 120 seconds; changes wait for their answer
without a read timeout, since an import can take minutes (D225).
"""

import urllib.parse
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, JsonValue, ValidationError

from aibi.core.operator.auth import OPERATOR_HEADER, encode_operator
from aibi.core.schema.curation import CurationQueue
from aibi.core.schema.jsonio import JsonError, parse_json
from aibi.core.schema.operator import (
    Datasets,
    DatasetState,
    DescriptorShown,
    DraftChanged,
    ErasedOut,
    ImportPublished,
    LoggedIssuance,
    ProposalsRejected,
    ProposersRan,
    Pruned,
    Refusals,
    Rejected,
    SessionEnded,
    SessionOpened,
    SessionPublishedOut,
    Uploaded,
    Withdrawn,
)
from aibi.core.schema.refusals import Refusal

READ_TIMEOUT = httpx.Timeout(120.0)
CHANGE_TIMEOUT = httpx.Timeout(30.0, read=None, write=None)
_CHUNK = 1 << 20


class Refused(Exception):  # noqa: N818 - the spec's word
    """The router refused the operation."""

    def __init__(self, status: int, refusals: Sequence[Refusal]) -> None:
        codes = ", ".join(str(refusal.code) for refusal in refusals)
        super().__init__(f"refused with {status}: {codes}")
        self.status = status
        self.refusals = tuple(refusals)


class Unreachable(Exception):  # noqa: N818 - a condition, as the CLI reports it
    """The server could not be reached, or did not answer as the operator router does."""


@dataclass(frozen=True)
class Answer[M: BaseModel]:
    """An operation's answer: read, and as it came."""

    value: M
    body: bytes


def _segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class OperatorClient:
    """The router's operations, over ``http``, whose base URL is the server's."""

    def __init__(self, http: httpx.Client, *, token: str, operator: str) -> None:
        self._http = http
        self._headers = {
            "Authorization": f"Bearer {token}",
            OPERATOR_HEADER: encode_operator(operator),
            "Accept-Encoding": "identity",
        }

    def _read[M: BaseModel](self, response: httpx.Response, model: type[M]) -> Answer[M]:
        if 300 <= response.status_code < 400:
            raise Unreachable("the server answered with a redirect, which is not followed")
        try:
            value = parse_json(response.content)
        except JsonError:
            raise Unreachable(f"the server answered {response.status_code} without JSON") from None
        if response.status_code >= 400:
            try:
                refusals = Refusals.model_validate(value).refusals
            except ValidationError:
                raise Unreachable(
                    f"the server answered {response.status_code} without refusals"
                ) from None
            raise Refused(response.status_code, refusals)
        try:
            return Answer(model.model_validate(value), response.content)
        except ValidationError:
            raise Unreachable("the server's answer is not the operator router's") from None

    def _send(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        timeout: httpx.Timeout,
        params: dict[str, str] | None = None,
        json: JsonValue = None,
        content: Iterator[bytes] | None = None,
    ) -> httpx.Response:
        request = self._http.build_request(
            method,
            path,
            params=params,
            json=json,
            content=content,
            headers=headers,
            timeout=timeout,
        )
        try:
            return self._http.send(request)
        except httpx.TransportError as error:
            raise Unreachable(f"cannot reach the server ({type(error).__name__})") from None
        except httpx.HTTPError as error:
            raise Unreachable(f"cannot read the server's answer ({type(error).__name__})") from None

    def _get[M: BaseModel](
        self, path: str, model: type[M], params: dict[str, str] | None = None
    ) -> Answer[M]:
        response = self._send(
            "GET", path, params=params, headers=self._headers, timeout=READ_TIMEOUT
        )
        return self._read(response, model)

    def _post[M: BaseModel](self, path: str, body: JsonValue, model: type[M]) -> Answer[M]:
        response = self._send(
            "POST",
            path,
            json=body,
            headers={**self._headers, "Content-Type": "application/json"},
            timeout=CHANGE_TIMEOUT,
        )
        return self._read(response, model)

    @staticmethod
    def _dataset(dataset: str, *rest: str) -> str:
        return "/".join(["/operator/datasets", _segment(dataset), *rest])

    # --- Reads ---

    def datasets(self) -> Answer[Datasets]:
        return self._get("/operator/datasets", Datasets)

    def dataset(self, dataset: str) -> Answer[DatasetState]:
        return self._get(self._dataset(dataset), DatasetState)

    def queue(self, dataset: str, *, release: int | None = None) -> Answer[CurationQueue]:
        params = None if release is None else {"release": str(release)}
        return self._get(self._dataset(dataset, "queue"), CurationQueue, params)

    def descriptor(
        self, dataset: str, descriptor: str, *, release: str | None = None
    ) -> Answer[DescriptorShown]:
        params = None if release is None else {"release": release}
        path = self._dataset(dataset, "descriptors", _segment(descriptor))
        return self._get(path, DescriptorShown, params)

    # --- Uploads and imports ---

    def upload(self, dataset: str, file: Path) -> Answer[Uploaded]:
        """Stream a local file into the dataset's upload area."""
        extension = file.suffix.lower().removeprefix(".")
        with file.open("rb") as opened:
            size = str(opened.seek(0, 2))
            opened.seek(0)

            def chunks() -> Iterator[bytes]:
                while chunk := opened.read(_CHUNK):
                    yield chunk

            headers = {
                **self._headers,
                "Content-Type": "application/octet-stream",
                "Content-Length": size,
            }
            response = self._send(
                "POST",
                self._dataset(dataset, "uploads"),
                params={"extension": extension},
                content=chunks(),
                headers=headers,
                timeout=CHANGE_TIMEOUT,
            )
        return self._read(response, Uploaded)

    def import_(
        self, dataset: str, source: dict[str, JsonValue], **options: str | None
    ) -> Answer[ImportPublished]:
        body: dict[str, JsonValue] = {"source": source}
        body.update({name: value for name, value in options.items() if value is not None})
        return self._post(self._dataset(dataset, "import"), body, ImportPublished)

    def reimport(
        self, dataset: str, source: dict[str, JsonValue], **options: str | None
    ) -> Answer[ImportPublished]:
        body: dict[str, JsonValue] = {"source": source}
        body.update({name: value for name, value in options.items() if value is not None})
        return self._post(self._dataset(dataset, "reimport"), body, ImportPublished)

    # --- Withdrawal, erasure and proposals ---

    def withdraw(self, dataset: str, release: int | str) -> Answer[Withdrawn]:
        return self._post(self._dataset(dataset, "withdraw"), {"release": release}, Withdrawn)

    def erase(
        self,
        dataset: str,
        table: str,
        key: Sequence[JsonValue],
        *,
        redact_only: bool = False,
    ) -> Answer[ErasedOut]:
        body: dict[str, JsonValue] = {"table": table, "key": list(key), "redact_only": redact_only}
        return self._post(self._dataset(dataset, "erase"), body, ErasedOut)

    def run_proposers(self, dataset: str) -> Answer[ProposersRan]:
        return self._post(self._dataset(dataset, "proposers"), {}, ProposersRan)

    def reject(self, dataset: str, proposal: int) -> Answer[Rejected]:
        path = self._dataset(dataset, "proposals", str(proposal), "reject")
        return self._post(path, {}, Rejected)

    def reject_all(
        self, dataset: str, *, proposer: str | None = None, kind: str | None = None
    ) -> Answer[ProposalsRejected]:
        body: dict[str, JsonValue] = {}
        if proposer is not None:
            body["proposer"] = proposer
        if kind is not None:
            body["kind"] = kind
        return self._post(self._dataset(dataset, "proposals", "reject"), body, ProposalsRejected)

    # --- The derivation log ---

    def issuance(self, issuance: str) -> Answer[LoggedIssuance]:
        return self._post("/operator/log/issuance", {"id": issuance}, LoggedIssuance)

    def prune(self, *, before: str | None = None) -> Answer[Pruned]:
        body: dict[str, JsonValue] = {} if before is None else {"before": before}
        return self._post("/operator/log/prune", body, Pruned)

    # --- Sessions ---

    def open(self, dataset: str) -> Answer[SessionOpened]:
        return self._post(self._dataset(dataset, "session", "open"), {}, SessionOpened)

    def take_over(self, dataset: str) -> Answer[SessionOpened]:
        return self._post(self._dataset(dataset, "session", "take-over"), {}, SessionOpened)

    def change(
        self, dataset: str, handle: str, expected: str, edits: Sequence[JsonValue]
    ) -> Answer[DraftChanged]:
        body: dict[str, JsonValue] = {"handle": handle, "expected": expected, "edits": list(edits)}
        return self._post(self._dataset(dataset, "session", "change"), body, DraftChanged)

    def publish(self, dataset: str, handle: str, expected: str) -> Answer[SessionPublishedOut]:
        body: dict[str, JsonValue] = {"handle": handle, "expected": expected}
        return self._post(self._dataset(dataset, "session", "publish"), body, SessionPublishedOut)

    def discard(self, dataset: str, handle: str, expected: str) -> Answer[SessionEnded]:
        body: dict[str, JsonValue] = {"handle": handle, "expected": expected}
        return self._post(self._dataset(dataset, "session", "discard"), body, SessionEnded)


__all__ = ["CHANGE_TIMEOUT", "READ_TIMEOUT", "Answer", "OperatorClient", "Refused", "Unreachable"]

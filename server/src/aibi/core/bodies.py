"""Request bodies received within deadlines (SPEC §14, D266, D278).

A body is refused if it sends nothing for ``idle`` seconds (``LIMIT_EXCEEDED`` naming
``upload_idle_seconds``, or for a tool call's body ``tool_body_idle_seconds``), or has not ended
``idle`` seconds and its length at the slowest rate the server accepts after it began
(``LIMIT_EXCEEDED`` naming ``upload_seconds``), so that a client that sends a body slowly, or half
of one, holds no connection and no place for long, while one that keeps sending is never refused
for its gaps alone (``Deadlines``). The operator
router receives its uploads and JSON bodies this way, with ``[imports]``'s
``upload_idle_seconds`` and ``upload_min_bytes_per_second``, and the tool transports (``/mcp`` and
``/api/tools/<name>``) theirs, whose bodies come with no token, with the shorter ``[server]
tool_body_idle_seconds`` and the same rate (D278).

A body's length is the one ``Content-Length`` it declares, when ``Transfer-Encoding``, which h11
lets override it, does not frame it (``declared_length``); a body that declares none is allowed
the time of the largest body request protection lets through.
"""

import math
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

import anyio

from aibi.core.schema.limits import UPLOAD_IDLE_SECONDS, UPLOAD_SECONDS
from aibi.core.schema.refusals import Limit, RefusalCode
from aibi.core.store.store import StoreRefused


def stalled(seconds: float, limit: str = UPLOAD_IDLE_SECONDS) -> StoreRefused:
    """The refusal of a body that sent nothing for ``seconds``, naming the setting ``limit``."""
    return StoreRefused(
        RefusalCode.LIMIT_EXCEEDED,
        f"The request's body sent nothing for {seconds:g} seconds",
        limit=Limit(name=limit, max=max(1, round(seconds))),
    )


def overdue(seconds: float) -> StoreRefused:
    """The refusal of a body that had not ended ``seconds`` after it began."""
    return StoreRefused(
        RefusalCode.LIMIT_EXCEEDED,
        f"The request's body did not end within {seconds:g} seconds, the time its length allows "
        "at the slowest rate the server accepts",
        limit=Limit(name=UPLOAD_SECONDS, max=max(1, math.ceil(seconds))),
    )


def declared_length(headers: Sequence[tuple[bytes, bytes]]) -> int | None:
    """The length a request's raw headers (ASGI's, names in lower case) declare in one
    ``Content-Length``, unless ``Transfer-Encoding`` frames the body."""
    if any(name == b"transfer-encoding" for name, _ in headers):
        return None
    given = [value for name, value in headers if name == b"content-length"]
    return int(given[0]) if len(given) == 1 and given[0].isdigit() else None


@dataclass(frozen=True)
class Deadlines:
    """A body's deadlines (D266): ``idle`` seconds between chunks (``upload_idle_seconds``), and
    ``allowed`` seconds in all, ``idle`` and its length at the slowest rate, ending at ``ends``
    on anyio's clock (``upload_seconds``)."""

    idle: float
    allowed: float
    ends: float
    idle_limit: str = UPLOAD_IDLE_SECONDS
    """The setting that ``idle`` is: ``upload_idle_seconds``, or for a tool call's body
    ``tool_body_idle_seconds`` (D278)."""

    @classmethod
    def of(
        cls,
        idle: float,
        min_bytes_per_second: int,
        length: int,
        *,
        idle_limit: str = UPLOAD_IDLE_SECONDS,
    ) -> "Deadlines":
        """The deadlines of a body of ``length`` bytes, beginning now."""
        allowed = idle + length / min_bytes_per_second
        return cls(idle, allowed, anyio.current_time() + allowed, idle_limit)

    async def next_chunk(self, stream: AsyncIterator[bytes]) -> bytes | None:
        """The body's next chunk, ``None`` at its end, or the refusal of a deadline passed."""
        left = self.ends - anyio.current_time()
        if left <= 0:
            raise overdue(self.allowed)
        try:
            with anyio.fail_after(min(self.idle, left)):
                return await anext(stream)
        except StopAsyncIteration:
            return None
        except TimeoutError:
            if self.idle < left:
                raise stalled(self.idle, self.idle_limit) from None
            raise overdue(self.allowed) from None

    async def read(self, stream: AsyncIterator[bytes]) -> bytes:
        """The whole body; request protection has bounded its size (D260)."""
        found = bytearray()
        while (chunk := await self.next_chunk(stream)) is not None:
            found += chunk
        return bytes(found)


__all__ = ["Deadlines", "declared_length", "overdue", "stalled"]

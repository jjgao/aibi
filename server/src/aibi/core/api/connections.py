"""Connections: a deadline for each request's head and for sending its answers, and a cap on each
client's connections (SPEC §14, D254, D255).

uvicorn counts every open connection against ``limit_concurrency`` (``max_connections``) when a
request's head arrives, idle ones included, and arms no timer on a connection before its first
response; so connections that stay open without sending a request, or that send part of one and
stop, would hold every place and lock every client out, the operator included, before request
protection sees a request. ``guarded_protocol`` gives uvicorn's h11 protocol four rules:

- **A deadline for the request head.** A connection that has no request under way
  ``request_head_seconds`` after it opened, or after its last response ended, is closed: it sent
  no request, or not the whole of one's head. A request whose head arrived is under way until
  its response ends, however long it runs; its body is the router's to time (D266).
- **Idle connections make room.** A connection that opens when the open connections have
  reached ``max_connections`` closes the connection that has been idle (no request under way)
  the longest, so that idle connections never keep a request out; it has no place only when
  every other connection has a request under way, and then uvicorn answers 503.
- **A cap per client.** A client (its address, keyed as ``client_key`` keys it) has at
  most ``max_connections_per_client`` connections open, fewer than ``max_connections``: a new
  one past the cap closes that client's longest idle connection, or, if every one of them has a
  request under way, is closed at once. So no one client holds every place.
- **A deadline for sending.** uvicorn waits, with no deadline, for a client to take what was
  written before it writes more, so a client that sends requests and never reads the answers
  would keep its connection busy, out of reach of the other rules. When the responses waiting
  for a client pass the transport's high-water mark (``pause_writing``), or a connection closed
  by these rules still holds some, the client must take ``MIN_SEND_BYTES_PER_SECOND`` of them a
  second, on average over each ``send_seconds``, until they fall below the low-water mark
  (``resume_writing``) or are all sent; a connection whose client takes less is aborted and its
  socket reset, so that neither it nor its unsent answers outlive the deadline. What a client
  took is what its end acknowledged (``tcpi_bytes_acked`` of Linux's ``TCP_INFO``), since the
  kernel refills its send buffer from the transport only after it has sent about a third of it,
  which a pipelining client reading tens of KiB a second may take longer than ``send_seconds``
  to do. Where ``TCP_INFO`` is not there, it is how far the transport's buffer fell, and then a
  client that reads less than about a third of the kernel's send buffer each ``send_seconds``
  can be reset.

uvicorn also answers 503 while the requests under way (its tasks) reach ``limit_concurrency``, and
a connection that pipelines holds two for a moment as one answer ends and the next request
starts; closing an idle connection frees none of them, so a request can meet a 503 while fewer
than ``max_connections`` connections are open, if others are all under way.

Everything runs on the event loop, so no lock is needed.
"""

import asyncio
import contextlib
import socket
import struct
from typing import ClassVar, cast

from uvicorn.protocols.http.h11_impl import H11Protocol, RequestResponseCycle

from aibi.core.api.rates import client_key

MIN_SEND_BYTES_PER_SECOND = 1024
"""The least a client must take of the responses waiting for it, on average over each
``send_seconds``, while they are over the high-water mark or its connection is closing."""
_BYTES_ACKED = 120
"""Where ``tcpi_bytes_acked``, a 64-bit count, sits in Linux's ``struct tcp_info``."""
_RESET = struct.pack("ii", 1, 0)
"""``SO_LINGER`` on, with no time: closing the socket resets it and drops what it holds."""


class GuardedProtocol(H11Protocol):
    """uvicorn's h11 protocol with the rules of the module docstring; ``guarded_protocol``
    makes one with its settings."""

    request_head_seconds: ClassVar[float] = 10.0
    max_connections_per_client: ClassVar[int] = 16
    send_seconds: ClassVar[float] = 30.0

    _key: str = ""
    _idle_since: float = 0.0
    _deadline: asyncio.TimerHandle | None = None
    _sending: asyncio.TimerHandle | None = None
    _taken: int = 0

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        super().connection_made(transport)
        self._key = client_key(self.client[0]) if self.client else ""
        self._idle_since = self.loop.time()
        self._make_room()
        if not self.transport.is_closing():
            self._arm()

    def connection_lost(self, exc: Exception | None) -> None:
        self._disarm()
        self._stop_sending_deadline()
        super().connection_lost(exc)

    def pause_writing(self) -> None:
        super().pause_writing()
        self._start_sending_deadline()

    def resume_writing(self) -> None:
        super().resume_writing()
        if not self.transport.is_closing():
            self._stop_sending_deadline()

    def on_response_complete(self) -> None:
        super().on_response_complete()
        self._idle_since = self.loop.time()
        self._arm()

    @property
    def busy(self) -> bool:
        """Whether a request is under way: its head arrived, and its response has not ended."""
        cycle = cast(RequestResponseCycle | None, self.cycle)
        return cycle is not None and not cycle.response_complete

    def _others(self) -> list["GuardedProtocol"]:
        return [
            found
            for found in cast(set[object], self.connections)
            if isinstance(found, GuardedProtocol) and found is not self
        ]

    def _make_room(self) -> None:
        others = self._others()
        mine = [found for found in others if found._key == self._key]
        if len(mine) >= self.max_connections_per_client and not _close_idlest(mine):
            self._close()
            return
        limit = self.config.limit_concurrency
        if limit is not None and len(others) + 1 >= limit:
            _close_idlest(others)

    def _arm(self) -> None:
        self._disarm()
        self._deadline = self.loop.call_later(self.request_head_seconds, self._expired)

    def _disarm(self) -> None:
        if self._deadline is not None:
            self._deadline.cancel()
            self._deadline = None

    def _expired(self) -> None:
        self._deadline = None
        if not self.busy:
            self._close()

    def _close(self) -> None:
        self._disarm()
        self.connections.discard(self)
        if not self.transport.is_closing():
            self.transport.close()
        if self.transport.get_write_buffer_size():
            self._start_sending_deadline()

    def _start_sending_deadline(self) -> None:
        if self._sending is None:
            self._taken = self._taken_so_far()
            self._sending = self.loop.call_later(self.send_seconds, self._sending_expired)

    def _stop_sending_deadline(self) -> None:
        if self._sending is not None:
            self._sending.cancel()
            self._sending = None

    def _taken_so_far(self) -> int:
        """A count that grows by what the client takes: what its end acknowledged, or, where
        the socket does not say, the transport's unsent bytes negated."""
        acked = _bytes_acked(self.transport.get_extra_info("socket"))
        return -self.transport.get_write_buffer_size() if acked is None else acked

    def _sending_expired(self) -> None:
        self._sending = None
        if not self.transport.get_write_buffer_size():
            return
        if self._taken_so_far() - self._taken >= MIN_SEND_BYTES_PER_SECOND * self.send_seconds:
            self._start_sending_deadline()
            return
        self.connections.discard(self)
        found = self.transport.get_extra_info("socket")
        if found is not None:
            with contextlib.suppress(OSError):
                found.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, _RESET)
        self.transport.abort()


def _bytes_acked(found: object) -> int | None:
    """``tcpi_bytes_acked`` of a TCP socket on Linux; ``None`` where the socket cannot say."""
    option = getattr(socket, "TCP_INFO", None)
    getsockopt = getattr(found, "getsockopt", None)
    if option is None or getsockopt is None:
        return None
    try:
        info = cast(bytes, getsockopt(socket.IPPROTO_TCP, option, 256))
    except OSError:
        return None
    if len(info) < _BYTES_ACKED + 8:
        return None
    (acked,) = struct.unpack_from("=Q", info, _BYTES_ACKED)
    return acked


def _close_idlest(candidates: list[GuardedProtocol]) -> bool:
    """Close the candidate that has been idle the longest; whether one was idle."""
    idle = [found for found in candidates if not found.busy]
    if not idle:
        return False
    min(idle, key=lambda found: found._idle_since)._close()  # pyright: ignore[reportPrivateUsage]
    return True


def guarded_protocol(
    *, request_head_seconds: float, max_connections_per_client: int, send_seconds: float
) -> type[GuardedProtocol]:
    """A ``GuardedProtocol`` with these settings, for uvicorn's ``http``."""
    return cast(
        type[GuardedProtocol],
        type(
            "GuardedProtocol",
            (GuardedProtocol,),
            {
                "request_head_seconds": float(request_head_seconds),
                "max_connections_per_client": max_connections_per_client,
                "send_seconds": float(send_seconds),
            },
        ),
    )


__all__ = ["MIN_SEND_BYTES_PER_SECOND", "GuardedProtocol", "guarded_protocol"]

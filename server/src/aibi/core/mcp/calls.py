"""Tool calls, as both transports run them (SPEC §11.1, §14, D277, D278, D280).

A call runs the tool (``catalog.tools.call``) in a worker thread, never on the event loop, among
at most ``TOOL_CALLS`` calls at once, which have places of their own so that tool calls cannot
take the threads the operator router and the imports use; one client, keyed as the rates key it
(``client_key``, D259), holds at most ``per_client`` of them (``client_tool_calls``), so that one
client's calls cannot take every place from the others. A call holds its place until its
function returns in its thread, not until it is answered, so that calls answered past their
limit cannot pile up threads: at most ``concurrent`` run at any time. Each call has one
wall-clock limit, ``TOOL_SECONDS``, from when its body was received, waiting and the transport's
own reading of the body included (``ends``): one that gets no place within it is answered
``LIMIT_EXCEEDED`` naming ``client_tool_calls`` or ``tool_calls``, and one that gets a place but
has not ended by then is answered ``LIMIT_EXCEEDED`` naming ``tool_seconds``, its thread, which
cannot be stopped, finishing in the background with its place (all 503 over HTTP). A call
answered before its thread began gives its place back at once, and its function never runs. The
thread runs with the call's deadline in ``catalog.service.DEADLINE``, so that what it starts
(a query worker's run, D301) ends by it and what it records it records only while it can still
be answered (D300).

A call's body is received within its deadlines (``bodies.Deadlines``, ``Calls.deadlines``): the
public body's idle time, ``body_idle_seconds`` (``[server] tool_body_idle_seconds``, 10 s by
default, shorter than an upload's since the tools take no token), and ``[imports]``'s
``upload_min_bytes_per_second``. ``propose_descriptor`` is admitted at its own rate per client
(``proposals``, ``[server.rates] proposals``, ``LIMIT_EXCEEDED`` naming ``proposal_requests``,
429), since an unauthenticated client could otherwise fill the queue, and is given the client's
key, by which agents' proposals are shared (D277). A call's refusals are its answer; anything
else it raises is answered ``INTERNAL_ERROR``, logged by its type alone, never quoted, since an
exception's message may hold what the request held.
"""

import logging
import threading
import time
from collections.abc import Callable
from functools import partial
from typing import Literal, Protocol, cast

import anyio
import anyio.from_thread
import anyio.to_thread

from aibi.core.bodies import Deadlines
from aibi.core.catalog.service import Catalog, Deadline, within
from aibi.core.catalog.tools import Tool, call
from aibi.core.schema.limits import (
    CLIENT_TOOL_CALLS,
    MAX_BODY_BYTES,
    PROPOSAL_REQUESTS,
    TOOL_BODY_IDLE_SECONDS,
    TOOL_CALLS,
    TOOL_SECONDS,
)
from aibi.core.schema.output import Output, text
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode

SECONDS = 30.0
"""A tool call's wall-clock limit (``tool_seconds``)."""
CONCURRENT = 8
"""Tool calls that run at once in a server (``tool_calls``)."""
PER_CLIENT = 2
"""Tool calls that one client runs at once (``client_tool_calls``): a quarter of the places."""
BODY_IDLE_SECONDS = 10.0
"""How long a tool call's body may send nothing, when the configuration does not say
(``[server] tool_body_idle_seconds``)."""
BODY_MIN_BYTES_PER_SECOND = 32 * 1024
"""The slowest a body may average, when the configuration does not say (``[imports]``'s
``upload_min_bytes_per_second``)."""


class Admission(Protocol):
    """A rate per client (``api.rates``): ``take`` admits a request from a client's address,
    returning ``None``, or the seconds until it would be admitted."""

    @property
    def per_minute(self) -> int: ...

    def take(self, client: str) -> float | None: ...


_logger = logging.getLogger(__name__)


def failed() -> Refusal:
    return Refusal(code=RefusalCode.INTERNAL_ERROR, path=None, message=[text("The server failed")])


def _limit(name: str, most: int, message: str) -> list[Refusal]:
    return [
        Refusal(
            code=RefusalCode.LIMIT_EXCEEDED,
            path=None,
            message=[text(message)],
            limit=Limit(name=name, max=most),
        )
    ]


class _Place:
    """A call's place, which the call's thread gives back when its function returns, or the
    event loop when the call is answered before its thread began."""

    def __init__(self, give_back: Callable[[], None]) -> None:
        self._give_back = give_back
        self._lock = threading.Lock()
        self._state: Literal["waiting", "running", "given back"] = "waiting"

    def run[T](self, function: Callable[[], T]) -> T | None:
        """In the worker thread: ``function``'s result, or ``None`` if the call was answered
        before its thread began."""
        with self._lock:
            if self._state != "waiting":
                return None
            self._state = "running"
        try:
            return function()
        finally:
            try:
                anyio.from_thread.run_sync(self._give_back)
            except RuntimeError:
                _logger.warning("a tool call ended after its event loop")

    def unstarted(self) -> bool:
        """On the event loop: whether the thread had not begun, which it then never does."""
        with self._lock:
            if self._state != "waiting":
                return False
            self._state = "given back"
            return True

    def give_back(self) -> None:
        """On the event loop: give back the place of a call answered before its thread began."""
        self._give_back()


class Calls:
    """The tool calls of one server: its catalogue, their limits and their places."""

    def __init__(
        self,
        catalog: Catalog,
        *,
        seconds: float = SECONDS,
        concurrent: int = CONCURRENT,
        body_idle_seconds: float = BODY_IDLE_SECONDS,
        body_min_bytes_per_second: int = BODY_MIN_BYTES_PER_SECOND,
        max_body_bytes: int = MAX_BODY_BYTES,
        proposals: Admission | None = None,
        per_client: int = PER_CLIENT,
        client_key: Callable[[str], str] = str,
    ) -> None:
        self.catalog = catalog
        self.seconds = seconds
        self.concurrent = concurrent
        self.per_client = per_client
        self.client_key = client_key
        self.body_idle_seconds = body_idle_seconds
        self.body_min_bytes_per_second = body_min_bytes_per_second
        self.max_body_bytes = max_body_bytes
        self.proposals = proposals
        self._free: anyio.Semaphore | None = None
        self._threads: anyio.CapacityLimiter | None = None
        self._clients: dict[str, anyio.Semaphore] = {}

    def deadlines(self, declared: int | None) -> Deadlines:
        """The deadlines of a call's body that declares ``declared`` bytes, beginning now (D266,
        D278); one that declares none is allowed the time of ``max_body_bytes``."""
        length = self.max_body_bytes if declared is None else declared
        return Deadlines.of(
            self.body_idle_seconds,
            self.body_min_bytes_per_second,
            length,
            idle_limit=TOOL_BODY_IDLE_SECONDS,
        )

    def _places(self) -> tuple[anyio.Semaphore, anyio.CapacityLimiter]:
        if self._free is None or self._threads is None:
            self._free = anyio.Semaphore(self.concurrent)
            self._threads = anyio.CapacityLimiter(self.concurrent)
        return self._free, self._threads

    def ends(self) -> float:
        """When a call whose body was received now must be answered (``tool_seconds``)."""
        return anyio.current_time() + self.seconds

    def _client(self, key: str) -> anyio.Semaphore:
        found = self._clients.get(key)
        if found is None:
            found = self._clients[key] = anyio.Semaphore(self.per_client)
        return found

    def _forget(self, key: str) -> None:
        """Forget a client that holds and awaits no place."""
        own = self._clients.get(key)
        if own is not None and own.value == self.per_client and not own.statistics().tasks_waiting:
            del self._clients[key]

    def _give_back(self, key: str, free: anyio.Semaphore) -> Callable[[], None]:
        """On the event loop: give a place back to the pool and to its client."""

        def give_back() -> None:
            free.release()
            self._clients[key].release()
            self._forget(key)

        return give_back

    async def _place(self, key: str, ends: float) -> _Place | list[Refusal]:
        """A place among the client's and the pool's, or the refusal of a call that got none by
        ``ends``."""
        seconds = max(1, round(self.seconds))
        free, _ = self._places()
        own = self._client(key)
        held = False
        with anyio.move_on_after(max(0.0, ends - anyio.current_time())):
            await own.acquire()
            held = True
            await free.acquire()
            return _Place(self._give_back(key, free))
        if held:
            own.release()
        self._forget(key)
        if not held:
            return _limit(
                CLIENT_TOOL_CALLS,
                self.per_client,
                f"No place for the call freed within {seconds} seconds: a client runs at most "
                f"{self.per_client} calls at once",
            )
        return _limit(
            TOOL_CALLS,
            self.concurrent,
            f"No place for the call freed within {seconds} seconds: "
            f"{self.concurrent} calls run at once",
        )

    async def run[T](
        self, function: Callable[[], T], *, client: str = "", ends: float | None = None
    ) -> T | list[Refusal]:
        """``function``'s result in a worker thread, or the refusal of a call from ``client``
        (its address) that got no place or ran past ``ends`` (``ends()`` by default), or of one
        that failed (module docstring)."""
        seconds = max(1, round(self.seconds))
        ends = self.ends() if ends is None else ends
        _, threads = self._places()
        place = await self._place(self.client_key(client), ends)
        if isinstance(place, list):
            return place
        # The thread learns the call's deadline on its own clock (D301).
        deadline = Deadline(time.monotonic() + max(0.0, ends - anyio.current_time()), self.seconds)
        try:
            with anyio.fail_after(max(0.0, ends - anyio.current_time())):
                found = await anyio.to_thread.run_sync(
                    partial(place.run, partial(within, deadline, function)),
                    abandon_on_cancel=True,
                    limiter=threads,
                )
        except TimeoutError:
            return _limit(TOOL_SECONDS, seconds, f"The call did not end within {seconds} seconds")
        except Exception as error:
            _logger.error("a tool call failed: %s", type(error).__name__)
            return [failed()]
        finally:
            if place.unstarted():
                place.give_back()
        return cast(T, found)

    async def tool(
        self, tool: Tool, body: bytes, client: str, *, ends: float | None = None
    ) -> Output | list[Refusal]:
        """The tool's answer to a body from ``client``, the connection's address, due by ``ends``
        (``tool_seconds`` from now by default)."""
        rate = self.proposals if tool.name == "propose_descriptor" else None
        if rate is not None and rate.take(client) is not None:
            message = "Too many proposals from this client; try again later"
            return _limit(PROPOSAL_REQUESTS, rate.per_minute, message)
        key = self.client_key(client)
        return await self.run(
            partial(call, self.catalog, tool, body, client=key), client=client, ends=ends
        )


__all__ = [
    "BODY_IDLE_SECONDS",
    "BODY_MIN_BYTES_PER_SECOND",
    "CONCURRENT",
    "PER_CLIENT",
    "SECONDS",
    "Admission",
    "Calls",
    "failed",
]

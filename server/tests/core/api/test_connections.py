"""Connections (SPEC §14, D254, D255): a deadline for each request's head and for sending its
answers, idle connections making room, and a cap on each client's connections, so that
connections that send nothing, half a request, or requests whose answers they never read lock no
one out; on a real server, and on the protocol with a transport the test plays."""

import asyncio
import contextlib
import http.client
import inspect
import re
import socket
import struct
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import uvicorn
from uvicorn.protocols.http.flow_control import FlowControl
from uvicorn.protocols.http.h11_impl import H11Protocol, RequestResponseCycle
from uvicorn.server import ServerState

from aibi.core.api.connections import MIN_SEND_BYTES_PER_SECOND, GuardedProtocol, guarded_protocol
from aibi.core.api.serve import uvicorn_config


@contextmanager
def serving(built: Any, send_buffer: int | None = None) -> Iterator[int]:
    """A real uvicorn serving ``built``'s application on a loopback socket, whose connections
    have ``send_buffer`` if it is given: its port."""
    sock = socket.socket()
    if send_buffer is not None:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, send_buffer)
    sock.bind(("127.0.0.1", 0))
    port: int = sock.getsockname()[1]
    served = uvicorn.Server(uvicorn_config(built.config, built.app))
    thread = threading.Thread(target=served.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not served.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.01)
        yield port
    finally:
        served.should_exit = True
        thread.join(30)
        sock.close()
    assert not thread.is_alive()


Connect = Callable[..., socket.socket]


@pytest.fixture
def connect() -> Iterator[Connect]:
    """Opens a connection to a port from a loopback address, closed after the test."""
    opened: list[socket.socket] = []

    def connecting(
        port: int, source: str = "127.0.0.1", receive_buffer: int | None = None
    ) -> socket.socket:
        made = socket.socket()
        opened.append(made)
        if receive_buffer is not None:
            made.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
        try:
            made.bind((source, 0))
        except OSError:
            pytest.skip(f"cannot send from {source}")
        made.connect(("127.0.0.1", port))
        return made

    yield connecting
    for made in opened:
        made.close()


def closed_within(opened: socket.socket, seconds: float) -> bool:
    """Whether the server closes the connection within ``seconds``."""
    opened.settimeout(seconds)
    try:
        return opened.recv(1) == b""
    except ConnectionResetError:
        return True
    except TimeoutError:
        return False


def read_answer(opened: socket.socket) -> bytes:
    """One answer from the connection, read to the end of its body: its head."""
    opened.settimeout(10)
    answer = b""
    while b"\r\n\r\n" not in answer:
        chunk = opened.recv(65536)
        assert chunk, answer
        answer += chunk
    head, _, body = answer.partition(b"\r\n\r\n")
    declared = re.search(rb"(?im)^content-length: *([0-9]+)", head)
    assert declared is not None, head
    while len(body) < int(declared.group(1)):
        chunk = opened.recv(65536)
        assert chunk, head
        body += chunk
    return head


def get(port: int, path: str, headers: dict[str, str]) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        connection.request("GET", path, headers=headers)
        answered = connection.getresponse()
        answered.read()
        return answered.status
    finally:
        connection.close()


HALF_A_HEAD = b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1"


def test_idle_and_half_sent_connections_keep_no_operator_out(
    make_app: Any, connect: Connect
) -> None:
    built = make_app(server={"max_connections": 8, "request_head_seconds": 2})
    with serving(built) as port:
        held = [connect(port) for _ in range(8)]
        for opened in held[::2]:
            opened.sendall(HALF_A_HEAD)
        time.sleep(0.2)
        started = time.monotonic()
        assert get(port, "/operator/datasets", built.headers()) == 200
        assert time.monotonic() - started < 2
        assert all(closed_within(opened, 4) for opened in held)


def test_a_connection_without_a_whole_request_head_is_closed_at_its_deadline(
    make_app: Any, connect: Connect
) -> None:
    built = make_app(server={"request_head_seconds": 1})
    with serving(built) as port:
        half = connect(port)
        half.sendall(HALF_A_HEAD)
        idle = connect(port)
        started = time.monotonic()
        assert closed_within(half, 5)
        assert closed_within(idle, 5)
        assert 0.8 < time.monotonic() - started < 3
        assert get(port, "/api/health", {}) == 200


def test_a_request_under_way_outlives_the_head_deadline(make_app: Any, connect: Connect) -> None:
    built = make_app(server={"request_head_seconds": 1})
    body = b"k\n" + b"1\n" * 20
    with serving(built) as port:
        opened = connect(port)
        head = (
            "POST /operator/datasets/d/uploads?extension=csv HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Authorization: {built.headers()['Authorization']}\r\n"
            f"Aibi-Operator: {built.headers()['Aibi-Operator']}\r\n"
            "Content-Type: application/octet-stream\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        )
        opened.sendall(head.encode("ascii"))
        for index in range(0, len(body), 6):
            time.sleep(0.25)
            opened.sendall(body[index : index + 6])
        opened.settimeout(10)
        answer = b""
        while chunk := opened.recv(65536):
            answer += chunk
    assert answer.startswith(b"HTTP/1.1 200 "), answer[:200]


def test_one_client_holds_at_most_its_share_of_connections(make_app: Any, connect: Connect) -> None:
    built = make_app(server={"max_connections": 8, "request_head_seconds": 10})
    with serving(built) as port:
        other = [connect(port, "127.0.0.2") for _ in range(4)]
        time.sleep(0.2)
        assert [closed_within(opened, 0.3) for opened in other] == [True, True, False, False]
        assert get(port, "/api/health", {}) == 200


def test_idle_connections_make_room_for_a_new_one(make_app: Any, connect: Connect) -> None:
    built = make_app(server={"max_connections": 8, "request_head_seconds": 10})
    with serving(built) as port:
        held = [connect(port, f"127.0.0.{2 + index // 2}") for index in range(8)]
        time.sleep(0.2)
        assert get(port, "/operator/datasets", built.headers()) == 200
        assert sum(closed_within(opened, 0.3) for opened in held) == 2


def test_a_client_whose_connections_are_all_under_way_gets_no_more(
    make_app: Any, connect: Connect
) -> None:
    built = make_app(server={"max_connections": 8}, imports={"concurrent": 3})
    with serving(built) as port:
        uploads = [connect(port, "127.0.0.2") for _ in range(2)]
        for opened in uploads:
            head = (
                "POST /operator/datasets/d/uploads?extension=csv HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"Authorization: {built.headers()['Authorization']}\r\n"
                f"Aibi-Operator: {built.headers()['Aibi-Operator']}\r\n"
                "Content-Type: application/octet-stream\r\n"
                "Content-Length: 100\r\n\r\nk\n"
            )
            opened.sendall(head.encode("ascii"))
        time.sleep(0.3)
        third = connect(port, "127.0.0.2")
        assert closed_within(third, 1)
        assert not any(closed_within(opened, 0.2) for opened in uploads)
        assert get(port, "/api/health", {}) == 200
        for opened in uploads:
            opened.close()


def upload_head(built: Any, port: int, length: int, *, close: bool) -> bytes:
    head = (
        "POST /operator/datasets/d/uploads?extension=csv HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"Authorization: {built.headers()['Authorization']}\r\n"
        f"Aibi-Operator: {built.headers()['Aibi-Operator']}\r\n"
        "Content-Type: application/octet-stream\r\n"
        f"Content-Length: {length}\r\n" + ("Connection: close\r\n" if close else "") + "\r\n"
    )
    return head.encode("ascii")


def test_a_keep_alive_connection_idle_after_its_answer_is_closed_at_its_deadline(
    make_app: Any, connect: Connect
) -> None:
    built = make_app(server={"request_head_seconds": 1})
    with serving(built) as port:
        opened = connect(port)
        opened.sendall(f"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
        assert read_answer(opened).startswith(b"HTTP/1.1 200 ")
        answered = time.monotonic()
        assert closed_within(opened, 4)
        assert 0.8 < time.monotonic() - answered < 2.5


def test_a_trickle_after_a_long_request_is_closed_at_the_head_deadline(
    make_app: Any, connect: Connect
) -> None:
    built = make_app(server={"request_head_seconds": 1})
    body = b"k\n" + b"1\n" * 20
    with serving(built) as port:
        opened = connect(port)
        opened.sendall(upload_head(built, port, len(body), close=False))
        for index in range(0, len(body), 6):
            time.sleep(0.25)
            opened.sendall(body[index : index + 6])
        assert read_answer(opened).startswith(b"HTTP/1.1 200 ")
        answered = time.monotonic()
        closed = False
        for byte in HALF_A_HEAD[:20]:
            with contextlib.suppress(OSError):
                opened.sendall(bytes([byte]))
            if closed_within(opened, 0.25):
                closed = True
                break
        assert closed
        assert time.monotonic() - answered < 2.5


FLOOD = b"GET /api/health HTTP/1.1\r\nHost: elsewhere.example\r\n\r\n"
"""A request the Host check refuses, which no rate charges."""


class Flood(threading.Thread):
    """Sends pipelined requests on a connection for up to a minute, and reads none of their
    answers; ``ended`` is the error that stopped it, if one did."""

    def __init__(self, opened: socket.socket) -> None:
        super().__init__(daemon=True)
        self.opened = opened
        self.ended: OSError | None = None

    def run(self) -> None:
        ends = time.monotonic() + 60
        self.opened.settimeout(60)
        try:
            while time.monotonic() < ends:
                self.opened.sendall(FLOOD * 1000)
        except OSError as error:
            self.ended = error


def test_a_client_that_never_reads_its_answers_is_cut_off_at_the_sending_deadline(
    make_app: Any, connect: Connect
) -> None:
    built = make_app(server={"max_connections": 8, "request_head_seconds": 10, "send_seconds": 1})
    with serving(built) as port:
        floods = [Flood(connect(port, receive_buffer=4096)) for _ in range(2)]
        for started in floods:
            started.start()
        for flooding in floods:
            flooding.join(90)
            assert not flooding.is_alive()
            assert isinstance(flooding.ended, ConnectionResetError | BrokenPipeError)
        assert get(port, "/operator/datasets", built.headers()) == 200


@pytest.mark.skipif(
    not hasattr(socket, "TCP_INFO"), reason="no TCP_INFO: the fallback measures the buffer"
)
def test_a_client_that_pipelines_and_reads_slowly_but_steadily_keeps_its_connection(
    make_app: Any, connect: Connect
) -> None:
    built = make_app(server={"request_head_seconds": 10, "send_seconds": 1})
    with serving(built, send_buffer=256 * 1024) as port:
        reader = connect(port, receive_buffer=4096)
        flood = Flood(reader)
        flood.start()
        reader.settimeout(5)
        taken = 0
        ends = time.monotonic() + 5
        try:
            while time.monotonic() < ends:
                time.sleep(0.25)
                chunk = reader.recv(4096)
                assert chunk, "the server closed the connection"
                taken += len(chunk)
        finally:
            with contextlib.suppress(OSError):
                reader.shutdown(socket.SHUT_RDWR)
            flood.join(10)
        assert flood.ended is None or not isinstance(flood.ended, ConnectionResetError)
        assert taken / 5 > MIN_SEND_BYTES_PER_SECOND * 8


# --- The protocol, with a transport the test plays -----------------------------------------------


class Peer:
    """The socket a transport reports: its peer, and the options set on it."""

    def __init__(self, address: str) -> None:
        self.address = address
        self.options: list[tuple[int, int, bytes]] = []
        self.acked: int | None = None
        """What the peer acknowledged, as ``TCP_INFO`` reports it; ``None``: the socket cannot
        say."""

    def getpeername(self) -> tuple[str, int, int, int]:
        return (self.address, 50000, 0, 0)

    def getsockname(self) -> tuple[str, int, int, int]:
        return ("::1", 8000, 0, 0)

    def setsockopt(self, level: int, option: int, value: bytes) -> None:
        self.options.append((level, option, value))

    def getsockopt(self, level: int, option: int, length: int) -> bytes:
        if self.acked is None or (level, option) != (socket.IPPROTO_TCP, TCP_INFO):
            raise OSError("not a TCP socket")
        return bytes(120) + struct.pack("=Q", self.acked) + bytes(length - 128)


TCP_INFO = 11
"""Linux's ``TCP_INFO`` option."""


class Played(asyncio.Transport):
    """A transport whose unsent bytes the test sets; it records how it was closed."""

    def __init__(self, address: str) -> None:
        super().__init__()
        self.peer = Peer(address)
        self.unsent = 0
        self.written = b""
        self.closed = False
        self.aborted = False

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return self.peer if name == "socket" else default

    def is_closing(self) -> bool:
        return self.closed or self.aborted

    def close(self) -> None:
        self.closed = True

    def abort(self) -> None:
        self.aborted = True

    def get_write_buffer_size(self) -> int:
        return self.unsent

    def write(self, data: bytes | bytearray | memoryview) -> None:
        self.written += bytes(data)

    def pause_reading(self) -> None:
        pass

    def resume_reading(self) -> None:
        pass


async def _ok(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


class Protocols:
    """Guarded protocols of one server, with these settings, on an event loop the test runs;
    each gets a transport whose peer is the address it is given."""

    def __init__(self, **settings: float) -> None:
        given = {"request_head_seconds": 10, "max_connections_per_client": 2, "send_seconds": 30}
        self.loop = asyncio.new_event_loop()
        self.state = ServerState()
        self.protocol = guarded_protocol(**{**given, **settings})
        self.config = uvicorn.Config(_ok, limit_concurrency=8, http=self.protocol, log_config=None)

    def open(self, address: str = "127.0.0.1") -> tuple[GuardedProtocol, Played]:
        made = self.protocol(
            config=self.config, server_state=self.state, app_state={}, _loop=self.loop
        )
        transport = Played(address)
        made.connection_made(transport)
        return made, transport

    def run_for(self, seconds: float) -> None:
        self.loop.run_until_complete(asyncio.sleep(seconds))


@pytest.fixture
def protocols() -> Iterator[Callable[..., Protocols]]:
    made: list[Protocols] = []

    def making(**settings: float) -> Protocols:
        made.append(Protocols(**settings))
        return made[-1]

    yield making
    for found in made:
        found.loop.close()


def test_the_cap_per_client_counts_an_ipv6_client_by_its_64(
    protocols: Callable[..., Protocols],
) -> None:
    server = protocols()
    opened = [server.open(address)[1] for address in ("2001:db8::1", "2001:db8::2")]
    elsewhere = server.open("2001:db8:0:1::1")[1]
    third = server.open("2001:db8::3")[1]
    assert [found.closed for found in (*opened, elsewhere, third)] == [True, False, False, False]


def test_a_client_that_takes_its_answers_keeps_its_connection_and_one_that_stops_is_reset(
    protocols: Callable[..., Protocols],
) -> None:
    server = protocols(send_seconds=0.2)
    protocol, transport = server.open()
    taken = 300
    assert taken > MIN_SEND_BYTES_PER_SECOND * 0.2
    transport.unsent = 100_000
    protocol.pause_writing()
    for _ in range(6):
        transport.unsent -= taken
        server.run_for(0.1)
    assert not transport.aborted
    server.run_for(0.5)
    assert transport.aborted
    assert transport.peer.options == [
        (socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    ]


def test_writing_that_resumes_stops_the_sending_deadline(
    protocols: Callable[..., Protocols],
) -> None:
    server = protocols(send_seconds=0.1)
    protocol, transport = server.open()
    transport.unsent = 100_000
    protocol.pause_writing()
    transport.unsent = 1000
    protocol.resume_writing()
    server.run_for(0.3)
    assert not transport.aborted


def test_a_client_whose_end_acknowledges_its_answers_keeps_its_connection_though_the_buffer_stays(
    protocols: Callable[..., Protocols], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "TCP_INFO", TCP_INFO, raising=False)
    server = protocols(send_seconds=0.2)
    protocol, transport = server.open()
    transport.peer.acked = 0
    transport.unsent = 100_000
    protocol.pause_writing()
    for _ in range(6):
        transport.peer.acked += 300
        server.run_for(0.1)
    assert not transport.aborted
    server.run_for(0.5)
    assert transport.aborted


def test_writing_that_resumes_while_the_connection_closes_keeps_the_sending_deadline(
    protocols: Callable[..., Protocols],
) -> None:
    server = protocols(request_head_seconds=0.1, send_seconds=0.2)
    protocol, transport = server.open()
    transport.unsent = 500
    server.run_for(0.15)
    assert transport.closed
    protocol.resume_writing()
    server.run_for(0.3)
    assert transport.aborted


def test_a_connection_closed_with_answers_unsent_is_reset_if_they_are_not_taken(
    protocols: Callable[..., Protocols],
) -> None:
    server = protocols(request_head_seconds=0.1, send_seconds=0.2)
    _, transport = server.open()
    transport.unsent = 500
    server.run_for(0.15)
    assert transport.closed
    assert not transport.aborted
    server.run_for(0.3)
    assert transport.aborted


def test_the_guard_sees_a_request_under_way_until_its_answer_ends(
    protocols: Callable[..., Protocols],
) -> None:
    server = protocols()
    protocol, transport = server.open()
    assert protocol in protocol.connections
    assert not protocol.busy
    protocol.data_received(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    assert isinstance(protocol.cycle, RequestResponseCycle)
    assert protocol.busy
    server.run_for(0.05)
    assert protocol.cycle.response_complete
    assert not protocol.busy
    assert transport.written.startswith(b"HTTP/1.1 200 ")


def test_the_uvicorn_internals_the_guard_reads_have_the_shapes_it_expects() -> None:
    assert uvicorn.__version__.startswith("0.53.")
    for name in (
        "connection_made",
        "connection_lost",
        "data_received",
        "on_response_complete",
        "pause_writing",
        "resume_writing",
    ):
        assert callable(getattr(H11Protocol, name)), name
    events = inspect.getsource(H11Protocol.handle_events)
    assert "len(self.connections) >= self.limit_concurrency" in events
    assert "on_response=self.on_response_complete" in events
    assert "self.cycle = RequestResponseCycle(" in events
    made = inspect.getsource(H11Protocol.__init__)
    assert "self.connections = server_state.connections" in made
    assert "self.cycle: RequestResponseCycle = None" in made
    assert "self.response_complete = False" in inspect.getsource(RequestResponseCycle.__init__)
    sent = inspect.getsource(RequestResponseCycle.send)
    assert "self.response_complete = True" in sent
    assert "await self.flow.drain()" in sent
    assert "self.flow.pause_writing()" in inspect.getsource(H11Protocol.pause_writing)
    assert "self.flow.resume_writing()" in inspect.getsource(H11Protocol.resume_writing)
    assert isinstance(ServerState().connections, set)
    assert set(inspect.signature(FlowControl).parameters) == {"transport"}

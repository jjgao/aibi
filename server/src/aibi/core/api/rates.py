"""Rate limits: a token bucket per client and class (SPEC §14, D259).

A client's bucket holds at most ``burst`` tokens and refills at ``per_minute`` a minute; a request
takes one, and a request that finds none is refused with how long until one is back. So the
requests a client makes in any window of *t* seconds are at most ``burst + per_minute·t/60``.
The client is the connection's address, an IPv6 one by its /64 (``client_key``): one host is
routinely given a whole /64, so keying by the whole address would let it pass every limit by
changing its low bits. Two kinds of IPv6 address are keyed otherwise, since a /64 of them holds
many hosts: a link-local one (fe80::/10), which every host on a link has in fe80::/64, by the
whole address and its zone; one of the well-known NAT64 prefix (64:ff9b::/96), an IPv4 client
a translator put in IPv6, by that IPv4 address, as an IPv4-mapped one is; and one of the
local-use NAT64 prefix (64:ff9b:1::/48, RFC 8215) by the whole address, since the translator
chooses where in it the IPv4 address sits (RFC 6052), and in each of those layouts the rest is
fixed, so the whole address stands for one IPv4 client. At most ``clients``
buckets are kept per class; the least recently seen is dropped first, which bounds memory when
many addresses are used (a dropped bucket starts full again). The clock is injected, so tests
need not wait.
"""

import ipaddress
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

IPV6_PREFIX = 64
"""The prefix length by which IPv6 clients are keyed."""
NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
"""The well-known NAT64 prefix (RFC 6052): its last 32 bits are an IPv4 address."""
LOCAL_NAT64 = ipaddress.IPv6Network("64:ff9b:1::/48")
"""The local-use NAT64 prefix (RFC 8215): a translator's IPv4 clients, at a place it chooses."""


def client_key(address: str) -> str:
    """The key of a client's address: an IPv4 address as it is (an IPv4-mapped or NAT64 IPv6
    address as its IPv4 one), a link-local IPv6 address or one of the local-use NAT64 prefix as
    it is, any other IPv6 address as its /64, and anything else, such as a Unix socket's empty
    address, as given."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(parsed, ipaddress.IPv4Address):
        return str(parsed)
    if parsed.ipv4_mapped is not None:
        return str(parsed.ipv4_mapped)
    if parsed in NAT64:
        return str(ipaddress.IPv4Address(int(parsed) & 0xFFFFFFFF))
    if parsed.is_link_local or parsed in LOCAL_NAT64:
        return str(parsed)
    return str(ipaddress.IPv6Network((parsed, IPV6_PREFIX), strict=False))


@dataclass(frozen=True)
class Rate:
    """Requests a client may make per minute, and at once."""

    per_minute: int
    burst: int

    def __post_init__(self) -> None:
        if self.per_minute < 1 or self.burst < 1:
            raise ValueError("a rate has at least 1 request a minute and a burst of at least 1")


class Buckets:
    """The buckets of one class, by client."""

    def __init__(
        self,
        rate: Rate,
        *,
        clients: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate = rate
        self._clients = clients
        self._clock = clock
        self._levels: OrderedDict[str, tuple[float, float]] = OrderedDict()
        """By client: the tokens left, and when they were counted."""
        self._lock = threading.Lock()

    def _level(self, client: str, now: float) -> float:
        found = self._levels.get(client)
        if found is None:
            return float(self.rate.burst)
        tokens, then = found
        refilled = tokens + max(0.0, now - then) * self.rate.per_minute / 60
        return min(float(self.rate.burst), refilled)

    def _keep(self, client: str, tokens: float, now: float) -> None:
        self._levels[client] = (tokens, now)
        self._levels.move_to_end(client)
        while len(self._levels) > self._clients:
            self._levels.popitem(last=False)

    def _wait(self, tokens: float) -> float:
        return (1 - tokens) * 60 / self.rate.per_minute

    def take(self, client: str) -> float | None:
        """Take a token for a request: ``None`` if one was there, else the seconds to wait."""
        with self._lock:
            now = self._clock()
            tokens = self._level(client, now)
            if tokens >= 1:
                self._keep(client, tokens - 1, now)
                return None
            self._keep(client, tokens, now)
            return self._wait(tokens)


__all__ = ["IPV6_PREFIX", "Buckets", "Rate", "client_key"]

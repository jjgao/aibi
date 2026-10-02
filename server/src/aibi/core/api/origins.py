"""Hosts and origins, parsed (SPEC §14, D254, D256, D257).

Pure functions over the text of a ``Host`` header, an ``Origin`` header or a configured value.
A host is a DNS name (labels of letters, digits and hyphens, neither starting nor ending with a
hyphen, no trailing dot), an IPv4 literal, or an IPv6 literal in brackets; it is compared in
lower case, IPv6 in its compressed form. Anything else is malformed, and a malformed value is
never allowed: when in doubt, refuse.
"""

import ipaddress
import re
from collections.abc import Iterable

from aibi.core.operator.auth import is_loopback_host

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})
"""The loopback names, always allowed as hosts (D256)."""
DEFAULT_PORTS = {"http": 80, "https": 443}

_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PORT = re.compile(r"^[0-9]{1,5}$")


def is_loopback(bind: str) -> bool:
    """Whether a bind address is the loopback interface: ``localhost``, 127.0.0.0/8 or ``::1``."""
    return is_loopback_host(bind)


def hostname(value: str) -> str | None:
    """A host without a port, normalised: a DNS name or IPv4 literal in lower case, or an IPv6
    literal in brackets (given with or without them) in its compressed form; ``None`` if
    ``value`` is none of those."""
    if value.startswith("[") and value.endswith("]"):
        return _ipv6(value[1:-1])
    if ":" in value:
        return _ipv6(value)
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError:
        pass
    lowered = value.lower()
    if not lowered.isascii() or len(lowered) > 253:
        return None
    labels = lowered.split(".")
    return lowered if all(_LABEL.fullmatch(label) for label in labels) else None


def _ipv6(literal: str) -> str | None:
    """An IPv6 literal in brackets; one with a zone (``%eth0``) names no host a client can use."""
    if "%" in literal:
        return None
    try:
        return f"[{ipaddress.IPv6Address(literal).compressed}]"
    except ValueError:
        return None


def _authority(value: str) -> tuple[str, int | None] | None:
    """The host and port of ``host[:port]``, or ``None`` if malformed."""
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return None
        host, rest = hostname(value[: end + 1]), value[end + 1 :]
    else:
        name, colon, port_text = value.partition(":")
        host, rest = hostname(name), colon + port_text
    if host is None:
        return None
    if not rest:
        return host, None
    if not rest.startswith(":") or _PORT.fullmatch(rest[1:]) is None:
        return None
    port = int(rest[1:])
    return (host, port) if 0 < port <= 65535 else None


def host_name(value: str) -> str | None:
    """The host of a ``Host`` header, normalised and without its port; ``None`` if malformed."""
    found = _authority(value)
    return None if found is None else found[0]


def origin(value: str) -> str | None:
    """A serialised origin, ``<scheme>://<host>[:<port>]``, normalised: the scheme and host in
    lower case and a default port left out; ``None`` for anything else, ``null`` or an origin
    with a path, a query, a fragment or user information included."""
    scheme, separator, rest = value.partition("://")
    scheme = scheme.lower()
    if not separator or scheme not in DEFAULT_PORTS or any(c in rest for c in "/?#@\\"):
        return None
    found = _authority(rest)
    if found is None:
        return None
    host, port = found
    if port is None or port == DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def own_origins(hosts: Iterable[str], *, tls: bool, port: int) -> frozenset[str]:
    """The server's own origins: each host with the server's scheme and port (D257)."""
    scheme = "https" if tls else "http"
    suffix = "" if port == DEFAULT_PORTS[scheme] else f":{port}"
    return frozenset(f"{scheme}://{host}{suffix}" for host in hosts)


__all__ = [
    "DEFAULT_PORTS",
    "LOOPBACK_HOSTS",
    "host_name",
    "hostname",
    "is_loopback",
    "origin",
    "own_origins",
]

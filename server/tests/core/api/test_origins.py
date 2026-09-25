"""Hosts and origins, parsed (SPEC §14, D256, D257): what is normalised, what is malformed, and
that parsing never raises."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aibi.core.api.origins import host_name, hostname, is_loopback, origin, own_origins


@pytest.mark.parametrize(
    ("header", "host"),
    [
        ("127.0.0.1:8000", "127.0.0.1"),
        ("LOCALHOST:8000", "localhost"),
        ("localhost", "localhost"),
        ("[::1]:8000", "[::1]"),
        ("[0:0:0:0:0:0:0:1]", "[::1]"),
        ("Aibi.Lab.Example.org", "aibi.lab.example.org"),
        ("127.0.0.1.attacker.example", "127.0.0.1.attacker.example"),
    ],
)
def test_a_host_header_gives_its_host_normalised(header: str, host: str) -> None:
    assert host_name(header) == host


@pytest.mark.parametrize(
    "header",
    [
        "",
        "localhost.",
        "user@127.0.0.1:8000",
        "::1",
        "[::1",
        "[::1]x",
        "127.0.0.1:",
        "127.0.0.1:0",
        "127.0.0.1:65536",
        "127.0.0.1:80:80",
        "*.example.org",
        "exa mple.org",
        "-bad.example.org",
        "ex" + chr(0xE4) + "mple.org",
        "[fe80::1%eth0]",
    ],
)
def test_a_malformed_host_header_gives_none(header: str) -> None:
    assert host_name(header) is None


def test_a_configured_hostname_has_no_port() -> None:
    assert hostname("aibi.example.org") == "aibi.example.org"
    assert hostname("2001:db8::1") == "[2001:db8::1]"
    assert hostname("[2001:DB8::1]") == "[2001:db8::1]"
    assert hostname("aibi.example.org:443") is None
    assert hostname("aibi.example.org.") is None


@pytest.mark.parametrize(
    ("given_origin", "normalised"),
    [
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("HTTP://LOCALHOST:8000", "http://localhost:8000"),
        ("https://aibi.example.org:443", "https://aibi.example.org"),
        ("http://aibi.example.org:80", "http://aibi.example.org"),
        ("http://[::1]:8000", "http://[::1]:8000"),
    ],
)
def test_an_origin_is_normalised(given_origin: str, normalised: str) -> None:
    assert origin(given_origin) == normalised


@pytest.mark.parametrize(
    "given_origin",
    [
        "null",
        "file://",
        "ftp://example.org",
        "http://example.org/",
        "http://example.org/path",
        "http://example.org?q",
        "http://example.org#f",
        "http://user@example.org",
        "http://example.org:0",
        "example.org",
        "http://",
    ],
)
def test_other_values_are_no_origin(given_origin: str) -> None:
    assert origin(given_origin) is None


def test_the_server_s_own_origins_leave_default_ports_out() -> None:
    hosts = ["127.0.0.1", "localhost", "[::1]"]
    assert own_origins(hosts, tls=False, port=8000) == {
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://[::1]:8000",
    }
    assert own_origins(["aibi.example.org"], tls=True, port=443) == {"https://aibi.example.org"}
    assert own_origins(["aibi.example.org"], tls=False, port=80) == {"http://aibi.example.org"}


def test_a_bind_is_loopback_on_localhost_127_8_and_ipv6_one_alone() -> None:
    assert all(is_loopback(bind) for bind in ("localhost", "127.0.0.1", "127.0.0.9", "::1"))
    assert not any(is_loopback(bind) for bind in ("0.0.0.0", "::", "10.0.0.5", "2001:db8::1"))


@given(st.text(max_size=80))
def test_host_parsing_never_raises_and_is_idempotent(value: str) -> None:
    found = host_name(value)
    assert found is None or host_name(found) == found


@given(st.text(max_size=80) | st.builds(lambda host: f"http://{host}", st.text(max_size=40)))
def test_origin_parsing_never_raises_and_is_idempotent(value: str) -> None:
    found = origin(value)
    assert found is None or origin(found) == found

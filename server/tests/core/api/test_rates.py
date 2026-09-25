"""Token buckets per client (SPEC §14, D259): bursts, refills, waits, the cap on the clients
tracked, and the bound on what any window admits."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aibi.core.api.rates import Buckets, Rate, client_key


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_burst_is_admitted_then_the_client_waits_for_a_refill() -> None:
    clock = Clock()
    buckets = Buckets(Rate(per_minute=60, burst=2), clock=clock)
    assert buckets.take("a") is None
    assert buckets.take("a") is None
    assert buckets.take("a") == pytest.approx(1.0)
    assert buckets.take("b") is None
    clock.now = 0.5
    assert buckets.take("a") == pytest.approx(0.5)
    clock.now = 1.0
    assert buckets.take("a") is None


def test_a_long_idle_refills_a_bucket_to_its_burst_and_no_further() -> None:
    clock = Clock()
    buckets = Buckets(Rate(per_minute=60, burst=2), clock=clock)
    assert buckets.take("a") is None
    clock.now = 600.0
    assert [buckets.take("a") for _ in range(3)] == [None, None, pytest.approx(1.0)]


def test_the_least_recently_seen_client_is_dropped_past_the_cap() -> None:
    clock = Clock()
    buckets = Buckets(Rate(per_minute=1, burst=1), clients=2, clock=clock)
    assert buckets.take("a") is None
    assert buckets.take("b") is None
    assert buckets.take("a") is not None
    assert buckets.take("c") is None
    assert buckets.take("b") is None
    assert buckets.take("a") is None


def test_a_rate_is_at_least_one() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        Rate(per_minute=0, burst=1)


@given(
    per_minute=st.integers(1, 600),
    burst=st.integers(1, 20),
    gaps=st.lists(st.floats(0, 5, allow_nan=False), min_size=1, max_size=60),
    window=st.floats(0.1, 60, allow_nan=False),
)
def test_no_window_admits_more_than_the_burst_and_the_refill(
    per_minute: int, burst: int, gaps: list[float], window: float
) -> None:
    clock = Clock()
    buckets = Buckets(Rate(per_minute=per_minute, burst=burst), clock=clock)
    admitted: list[float] = []
    for gap in gaps:
        clock.now += gap
        if buckets.take("client") is None:
            admitted.append(clock.now)
    for start in admitted:
        inside = [at for at in admitted if start <= at <= start + window]
        assert len(inside) <= burst + per_minute * window / 60 + 1e-9


@pytest.mark.parametrize(
    ("address", "key"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("10.1.2.3", "10.1.2.3"),
        ("::1", "::/64"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
        ("2001:DB8:1:2::9", "2001:db8:1:2::/64"),
        ("fe80::1%eth0", "fe80::1%eth0"),
        ("FE80::2", "fe80::2"),
        ("::ffff:10.1.2.3", "10.1.2.3"),
        ("64:ff9b::192.0.2.1", "192.0.2.1"),
        ("64:ff9b::c000:202", "192.0.2.2"),
        ("64:ff9b:0:0:1::1", "64:ff9b::/64"),
        ("64:ff9b:1::c000:201", "64:ff9b:1::c000:201"),
        ("64:ff9b:1::c000:202", "64:ff9b:1::c000:202"),
        ("64:ff9b:1:c000:2:100::", "64:ff9b:1:c000:2:100::"),
        ("64:ff9b:1:c000:2:200::", "64:ff9b:1:c000:2:200::"),
        ("64:ff9b:2::1", "64:ff9b:2::/64"),
        ("", ""),
        ("not an address", "not an address"),
    ],
)
def test_a_client_is_keyed_by_its_address_an_ipv6_one_by_its_slash_64_unless_link_local_or_nat64(
    address: str, key: str
) -> None:
    assert client_key(address) == key

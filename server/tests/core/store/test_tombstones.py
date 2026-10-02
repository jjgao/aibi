"""Tombstones (SPEC §12.3, D240): the blob of a release's removals, canonical and sorted."""

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aibi.core.schema.jsonio import canonical
from aibi.core.store.tombstones import TOMBSTONES_FORMAT, Tombstone, decode, encode

TEXT = st.text(st.characters(blacklist_categories=["Cs"]), max_size=5)
JSON = st.recursive(
    st.none() | st.booleans() | st.integers(-(2**53) + 1, 2**53 - 1) | TEXT,
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(TEXT, inner, max_size=3),
    max_leaves=6,
)
TOMBSTONE = st.builds(
    Tombstone,
    st.sampled_from(["t", "t.a", "rel:t.a", "cov:t.a"]),
    st.sampled_from(["", "/label", "/fields/role", "/extensions/p/m"]),
    JSON,
    st.integers(1, 9),
)


def test_a_release_without_tombstones_has_no_blob() -> None:
    assert encode([]) is None


def test_the_blob_is_sorted_by_descriptor_and_pointer_in_canonical_form() -> None:
    later = Tombstone("t.b", "/fields/units", "kg", 2)
    earlier = Tombstone("t.a", "", {"/label": "a"}, 1)
    data = encode([later, earlier])
    assert data is not None
    parsed = json.loads(data)
    assert parsed["format"] == TOMBSTONES_FORMAT
    assert [t["descriptor"] for t in parsed["tombstones"]] == ["t.a", "t.b"]
    assert canonical(parsed) == data
    assert decode(data) == (earlier, later)


def test_two_tombstones_of_one_field_are_refused() -> None:
    with pytest.raises(ValueError, match="same field"):
        encode([Tombstone("t", "/label", "a", 1), Tombstone("t", "/label", "b", 1)])


@pytest.mark.parametrize(
    "data",
    [
        b"[]",
        b'{"format":"other","tombstones":[]}',
        b'{"format":"aibi.tombstones/1","tombstones":[{"descriptor":"t"}]}',
        b'{"tombstones":[],"format":"aibi.tombstones/1"}',
        b'{"format":"aibi.tombstones/1","tombstones":[{"descriptor":"t","inferred":1,'
        b'"pointer":"","version":true}]}',
    ],
)
def test_anything_but_a_blob_encode_wrote_is_refused(data: bytes) -> None:
    with pytest.raises(ValueError, match="tombstone"):
        decode(data)


@given(st.lists(TOMBSTONE, max_size=6, unique_by=lambda t: t.key))
def test_tombstones_round_trip_and_their_bytes_are_canonical(found: list[Tombstone]) -> None:
    data = encode(found)
    if not found:
        assert data is None
        return
    assert data is not None
    assert decode(data) == tuple(sorted(found, key=lambda t: t.key))
    assert canonical(json.loads(data)) == data
    assert encode(reversed(found)) == data

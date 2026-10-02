"""Ids from source names, as the importers assign them (SPEC §5.1, D191, D226).

The rules one by one are in ``tests/core/schema/test_ids.py``; these are the cases the file
importer adds, and the properties every assignment has."""

from hypothesis import given
from hypothesis import strategies as st

from aibi.core.schema.ids import is_identifier, normalise_names
from aibi.core.schema.limits import MAX_IDENTIFIER


def test_compatibility_forms_are_folded_first() -> None:
    assert normalise_names(["\ufb01le", "\uff33\uff49\uff5a\uff45"], "column") == ["file", "size"]


def test_a_suffix_after_a_cut_never_makes_a_double_underscore() -> None:
    long = "a" * 61 + "_b_c"
    first, second = normalise_names([long, long], "column")
    assert first == "a" * 61 + "_b"  # cut to 64, then the trailing _ removed
    assert second == "a" * 61 + "_2"
    assert "__" not in second


def test_repeated_and_empty_names_keep_their_ids_on_re_import() -> None:
    before = ["", "Name", "name", ""]
    first = normalise_names(before, "column")
    assert first == ["c_1", "name", "name_2", "c_4"]
    previous = list(zip(before, first, strict=True))
    again = normalise_names(["name", "", "Extra", "", "Name"], "column", previous)
    assert again == ["name_2", "c_1", "extra", "c_4", "name"]


@given(st.lists(st.text(max_size=80), max_size=12), st.sampled_from(["table", "column"]))
def test_every_assignment_gives_distinct_identifiers(names: list[str], kind: str) -> None:
    ids = normalise_names(names, "table" if kind == "table" else "column")
    assert len(set(ids)) == len(ids) == len(names)
    assert all(is_identifier(found) and len(found) <= MAX_IDENTIFIER for found in ids)
    if kind == "table":
        assert "dataset" not in ids

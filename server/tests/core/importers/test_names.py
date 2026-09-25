"""Ids from source names, as the importers assign them (SPEC §5.1, D191, D226).

The rules one by one are in ``tests/core/schema/test_ids.py``; these are the cases the file
importer adds, and the properties every assignment has."""

from types import SimpleNamespace
from typing import Any, cast

from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter

from aibi.core.importers.run import previous_names
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.ids import is_identifier, normalise_names
from aibi.core.schema.limits import MAX_IDENTIFIER
from aibi.core.store.manifest import Manifest

_DESCRIPTOR: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)


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


def _tables(names: list[str], ids: list[str]) -> list[Descriptor]:
    """Tables as an importer writes them, their ``/label`` inferred as their source name."""
    entry = {"status": "imported", "by": "importer:test@1", "at": "2026-01-01T00:00:00Z"}
    return [
        _DESCRIPTOR.validate_python(
            {
                "kind": "table",
                "id": identifier,
                "version": 1,
                "label": name or identifier,
                "fields": {},
                "curation": {"/label": {**entry, "inferred": name}},
            }
        )
        for name, identifier in zip(names, ids, strict=True)
    ]


_NO_TABLES = cast(Manifest, SimpleNamespace(tables=()))


def test_repeated_long_table_names_keep_their_ids_on_re_import() -> None:
    long = "a" * 70
    ids = normalise_names([long, long], "table")
    assert ids == ["a" * 64, "a" * 62 + "_2"]
    previous = previous_names(list(reversed(_tables([long, long], ids))), _NO_TABLES)
    assert previous.tables == ((long, "a" * 64), (long, "a" * 62 + "_2"))
    assert normalise_names([long, long], "table", previous.tables) == ids


def test_suffixes_are_ordered_as_numbers_within_one_name() -> None:
    names = ["x_5"] * 3 + ["a"] * 11
    ids = normalise_names(names, "table")
    assert ids[:3] == ["x_5", "x_5_2", "x_5_3"]
    previous = previous_names(_tables(names, ids)[::-1], _NO_TABLES)
    assert [identifier for _, identifier in previous.tables] == [
        "a",
        *(f"a_{n}" for n in range(2, 12)),
        "x_5",
        "x_5_2",
        "x_5_3",
    ]
    assert normalise_names(names, "table", previous.tables) == ids


@given(
    st.lists(
        st.sampled_from(["a", "A", "a_2", "Dataset", "x" * 70, "x" * 63 + "_2", "1a"]),
        max_size=12,
    ),
    st.randoms(use_true_random=False),
)
def test_every_table_keeps_its_id_on_an_unchanged_re_import(names: list[str], order: Any) -> None:
    ids = normalise_names(names, "table")
    tables = _tables(names, ids)
    order.shuffle(tables)
    previous = previous_names(tables, _NO_TABLES)
    assert normalise_names(names, "table", previous.tables) == ids

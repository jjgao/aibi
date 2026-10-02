"""What the file importer proposes (SPEC §5.3–§5.6, §13.1, D227–D229)."""

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any

import pytest

import aibi
from aibi.core.importers.infer import (
    ColumnGuess,
    Inferred,
    SourceTable,
    TableGuess,
    infer,
)
from aibi.core.schema.descriptors import ListSyntax
from aibi.core.store.sources import SourceValue


def _table(id: str, columns: str, *rows: Sequence[SourceValue]) -> SourceTable:
    return SourceTable(id, tuple(columns.split()), [tuple(row) for row in rows])


def _one(*values: SourceValue) -> ColumnGuess:
    [table] = infer([_table("t", "c", *[(value,) for value in values])]).tables
    return table.columns["c"]


def _tables(inferred: Inferred) -> dict[str, TableGuess]:
    return {table.id: table for table in inferred.tables}


def _notes(inferred: Inferred) -> list[tuple[str | None, str]]:
    return [
        (note.subject, "".join(s.model_dump().get("text", "") for s in note.message))
        for note in inferred.notes
    ]


@pytest.mark.parametrize(
    ("values", "datatype"),
    [
        (["1", "2", "-3"], "integer"),
        (["0", "1", "1", "0"], "integer"),
        ([45.0, 32.0], "integer"),
        (["1.5", "2", "1e3"], "number"),
        (["2024-01-02", "2023-12-31"], "date"),
        (["2024-01-02T10:00:00+02:00", "2024-01-03 11:00:00Z"], "datetime"),
        (["yes", "no", "YES"], "boolean"),
        (["TRUE", "FALSE"], "boolean"),
        (["Yes", "No"], "boolean"),
        ([True, False], "boolean"),
        (["1", "two"], "string"),
    ],
)
def test_each_datatype_is_the_first_that_parses_every_cell(
    values: list[SourceValue], datatype: str
) -> None:
    guess = _one(*values, *values, *values)
    assert (guess.datatype if guess.datatype != "category" else "string") == datatype
    assert guess.status == "proposed"


def test_a_datetime_without_an_offset_is_imported_by_default() -> None:
    naive = _one(datetime(2024, 1, 2, 10, 0), datetime(2024, 1, 3, 11, 0))
    assert (naive.datatype, naive.status) == ("datetime", "imported_default")
    text = _one("2024-01-02T10:00:00", "2024-01-02T11:00:00Z")
    assert (text.datatype, text.status) == ("datetime", "imported_default")
    aware = _one(datetime(2024, 1, 2, 10, tzinfo=timezone(timedelta(hours=1))))
    assert (aware.datatype, aware.status) == ("datetime", "proposed")


def test_missing_cells_never_make_a_datetime_naive() -> None:
    aware = _one("2024-01-02T10:00:00Z", "NA", None, "", "2024-01-03T10:00:00+01:00")
    assert (aware.datatype, aware.status) == ("datetime", "proposed")


def test_a_column_with_nothing_present_has_no_datatype() -> None:
    assert _one(None, "", "NA").datatype is None


def test_conventional_codes_are_declared_and_others_counted_not_declared() -> None:
    values = [str(n) for n in range(19)] + ["NA", "n/a", "refused"]
    guess = _one(*values)
    assert guess.datatype == "integer"
    assert dict(guess.missing_codes) == {"NA": "UNKNOWN", "n/a": "UNKNOWN"}
    assert "18 of 19 non-missing cells" not in guess.evidence
    assert "19 of 20 non-missing cells parse as integer" in guess.evidence
    assert "refused" not in guess.evidence
    too_many = _one(*[str(n) for n in range(18)], "refused", "declined")
    assert too_many.datatype == "string"


def test_a_datatype_that_parses_every_cell_beats_one_that_tolerates_a_stray_token() -> None:
    numbers = _one(*[str(n) for n in range(24)], "3.5")
    assert numbers.datatype == "number"
    assert "25 of 25 non-missing cells parse as number" in numbers.evidence
    days = [f"2024-01-{d:02d}" for d in range(1, 29)]
    assert _one(*days, "2024-02-01T10:00:00Z").datatype == "datetime"
    stray = _one(*[str(n) for n in range(24)], "3.5", "n.d.")
    assert stray.datatype == "number"
    assert "25 of 26 non-missing cells parse as number" in stray.evidence
    assert "no datatype parses them all" in stray.evidence


def test_the_tolerance_never_types_a_key_or_a_foreign_key() -> None:
    ids = [str(n) for n in range(1, 25)] + ["A-25"]
    members = _table("members", "member_id age", *[(i, str(20 + n % 3)) for n, i in enumerate(ids)])
    loans = _table("loans", "loan_id member_id", *[(f"l{n}", i) for n, i in enumerate(ids)])
    inferred = infer([members, loans])
    tables = _tables(inferred)
    assert tables["members"].key == ("member_id",)
    assert tables["members"].columns["member_id"].datatype == "string"
    assert tables["loans"].columns["member_id"].datatype == "string"
    assert [(r.id, r.parent) for r in inferred.relationships] == [
        ("rel:loans.member_id", "members")
    ]
    counts = _table("counts", "count_id n", *[(f"c{k}", v) for k, v in enumerate(ids)])
    guess = _tables(infer([counts]))["counts"]
    assert guess.key == ("count_id",)
    assert guess.columns["n"].datatype == "integer"  # neither a key nor a foreign key


def test_a_column_of_many_distinct_strings_is_typed_in_linear_time() -> None:
    rows = [(f"name {n}", f"note {n}") for n in range(100_000)]
    started = perf_counter()
    [table] = infer([_table("t", "name note", *rows)]).tables
    assert perf_counter() - started < 10
    assert table.columns["note"].datatype == "string"


def test_lists_are_found_in_each_syntax() -> None:
    assert _one('["a", "b"]', '["c"]', "[]").list_syntax == ListSyntax(format="json")
    assert _one("['a', 'b']", "('c',)").list_syntax == ListSyntax(format="python")
    delimited = _one("poetry;maths", "maths", "maths;chess", "chess")
    assert delimited.datatype == "list<category>"
    assert delimited.list_syntax == ListSyntax(format="delimited", delimiter=";")
    assert _one("a;b", "c;d", "e").list_syntax is None  # splitting finds no repeated item


def test_a_boolean_needs_one_word_and_then_reads_ones_and_zeros_too() -> None:
    assert _one("yes", "0", "1", "0").datatype == "boolean"
    assert _one("1", "0", "1", "0").datatype == "integer"


def test_splitting_must_repeat_an_item_to_make_a_list() -> None:
    assert _one("a;b", "a", "b;c").list_syntax is None  # 3 items for 3 distinct cells
    assert _one("a;b", "a", "b;a").list_syntax == ListSyntax(format="delimited", delimiter=";")


def test_a_category_has_few_values_for_its_cells() -> None:
    assert _one("red", "blue", "red", "blue").datatype == "category"
    assert _one("red", "blue", "green").datatype == "string"
    many = [f"v{n % 60}" for n in range(200)]
    assert _one(*many).datatype == "string"
    assert _one(*[f"v{n % 50}" for n in range(100)]).datatype == "category"
    assert _one(*[f"v{n % 51}" for n in range(102)]).datatype == "string"


def test_identifiers_need_twenty_distinct_values() -> None:
    names = [(f"n{n}", f"person {n}") for n in range(20)]
    inferred = infer([_table("people", "id name", *names)])
    name = _tables(inferred)["people"].columns["name"]
    assert name.identifier
    assert name.identifier_evidence == "All 20 present values are distinct (D228)"
    few = infer(
        [_table("people", "id name nick", *[(*n, f"x{i}") for i, n in enumerate(names[:5])])]
    )
    assert not _tables(few)["people"].columns["name"].identifier
    assert _notes(few) == [
        (
            "people",
            "No identifier is proposed among name, nick: their present values are distinct, but "
            "fewer than 20, too few to tell (D228)",
        )
    ]
    assert _notes(infer([_table("people", "id name", names[0])])) == []
    assert [subject for subject, _ in _notes(infer([_table("people", "id name", *names[:2])]))] == [
        "people"
    ]


SHELVES = _table("shelves", "shelf_id room", ("s1", "a"), ("s2", "a"))
BOOKS = _table("books", "book_id title", ("b1", "x"), ("b2", "y"), ("b3", "z"))
COPIES = _table("copies", "copy_id book_id", ("c1", "b1"), ("c2", "b1"), ("c3", "b2"))
SHELVINGS = _table(
    "shelvings", "book_id shelf_id", ("b1", "s1"), ("b2", "s1"), ("b1", "s2"), ("b3", "s2")
)
LOANS = _table(
    "loans",
    "loan_id copy_id borrowed",
    (1, "c1", "2024-01-02"),
    (2, "c1", "2024-02-03"),
    (3, "c3", "2024-03-04"),
)


def test_keys_relationships_roles_grain_and_coverage() -> None:
    inferred = infer([SHELVES, BOOKS, COPIES, SHELVINGS, LOANS])
    tables = _tables(inferred)
    assert {t: tables[t].key for t in tables} == {
        "shelves": ("shelf_id",),
        "books": ("book_id",),
        "copies": ("copy_id",),
        "shelvings": ("book_id", "shelf_id"),
        "loans": ("loan_id",),
    }
    assert {t: tables[t].role for t in tables} == {
        "shelves": "entity",
        "books": "entity",
        "copies": "entity",
        "shelvings": "link",
        "loans": "event",
    }
    assert [(r.id, r.parent, r.one_to_one, r.coverage) for r in inferred.relationships] == [
        ("rel:copies.book_id", "books", False, True),
        ("rel:shelvings.book_id", "books", False, True),
        ("rel:shelvings.shelf_id", "shelves", False, True),
        ("rel:loans.copy_id", "copies", False, False),
    ]
    assert inferred.relationships[0].evidence.startswith("Each of the column's 3 present values")
    assert tables["shelvings"].key_evidence is not None
    assert "first pair of foreign keys" in tables["shelvings"].key_evidence
    assert tables["copies"].columns["book_id"].datatype == "string"  # never a category


def test_a_unique_child_column_is_one_to_one() -> None:
    card = _table("cards", "card_id book_id", ("k1", "b1"), ("k2", "b2"))
    [relationship] = infer([BOOKS, card]).relationships
    assert relationship.one_to_one
    assert "one-to-one" in relationship.evidence


def test_ambiguous_key_to_key_and_self_containment_are_noted_not_proposed() -> None:
    twin = _table("twins", "book_id size", ("b1", 1), ("b2", 2), ("b3", 3))
    mirrored = infer([BOOKS, twin, COPIES])
    assert mirrored.relationships == ()
    assert [note for note in _notes(mirrored) if "D229" in note[1]] == [
        (
            "books.book_id",
            "The column is its table's key, and its values are keys of twins: a relationship "
            "between two tables' keys is never proposed, since two keys' values fit by chance "
            "(D229)",
        ),
        (
            "twins.book_id",
            "The column is its table's key, and its values are keys of books: a relationship "
            "between two tables' keys is never proposed, since two keys' values fit by chance "
            "(D229)",
        ),
        (
            "copies.book_id",
            "The column's values are keys of 2 tables, so no relationship is proposed: a join "
            "path is never picked silently (D229)",
        ),
    ]
    staff = _table("staff", "staff_id manager", ("a", "a"), ("b", "a"), ("c", "b"))
    assert infer([staff]).relationships == ()
    [note] = _notes(infer([staff]))
    assert note == (
        "staff.manager",
        "The column's values are keys of its own table; a relationship to itself is not "
        "proposed (D229)",
    )


def test_a_key_contained_in_another_table_s_key_is_no_foreign_key() -> None:
    books = _table("books", "id title", *[(n, f"t{n}") for n in (1, 2, 3)])
    members = _table("members", "id name", *[(n, f"m{n}") for n in (1, 2, 3, 4, 5)])
    cards = _table("cards", "card_id colour", ("m1", "red"), ("m2", "red"))
    people = _table("people", "person_id", ("m1",), ("m2",), ("m3",))
    inferred = infer([books, members, cards, people])
    assert inferred.relationships == ()
    assert {t.id: t.role for t in inferred.tables} == {
        "books": "entity",
        "members": "entity",
        "cards": "entity",
        "people": "entity",
    }
    assert [subject for subject, text in _notes(inferred) if "two tables' keys" in text] == [
        "books.id",
        "cards.card_id",
    ]


def test_integers_match_only_a_key_of_their_name() -> None:
    rooms = _table("rooms", "id size", (1, "s"), (2, "m"), (3, "l"))
    guarded = _table("desks", "desk_id floor", (10, 1), (11, 2), (12, 1))
    named = _table("chairs", "chair_id rooms_id", (20, 1), (21, 3))
    singular = _table("lamps", "lamp_id room_id", (30, 1), (31, 1))
    bare = _table("mats", "mat_id id", ("a", 2), ("b", 3))
    inferred = infer([rooms, guarded, named, singular, bare])
    assert [r.id for r in inferred.relationships] == [
        "rel:chairs.rooms_id",
        "rel:lamps.room_id",
        "rel:mats.id",
    ]
    assert (
        "desks.floor",
        "The column's integers are keys of rooms, but its id is none of the key's, rooms_<key> "
        "and room_<key>, so no relationship is proposed: small numbers are contained by chance "
        "(D229)",
    ) in _notes(inferred)


@pytest.mark.parametrize(
    ("parent", "column"),
    [
        ("books", "book_id"),
        ("boxes", "box_id"),
        ("classes", "class_id"),
        ("branches", "branch_id"),
        ("wishes", "wish_id"),
        ("buzzes", "buzz_id"),
        ("shoes", "shoe_id"),
        ("staff", "staff_id"),
    ],
)
def test_an_integer_foreign_key_may_name_its_parent_in_the_singular(
    parent: str, column: str
) -> None:
    parents = _table(parent, "id name", (1, "a"), (2, "b"), (3, "c"))
    loans = _table("loans", f"loan_id {column}", (10, 1), (11, 3), (12, 1))
    [relationship] = infer([parents, loans]).relationships
    assert (relationship.id, relationship.parent, relationship.parent_columns) == (
        f"rel:loans.{column}",
        parent,
        ("id",),
    )


def test_loans_find_their_books_and_members_by_singular_names() -> None:
    books = _table("books", "id title", *[(n, f"t{n}") for n in (1, 2, 3)])
    members = _table("members", "id name", *[(n, f"m{n}") for n in (1, 2, 3, 4, 5)])
    loans = _table("loans", "loan_id book_id member_id", (7, 1, 5), (8, 3, 5), (9, 3, 1))
    inferred = infer([books, members, loans])
    assert [(r.id, r.parent) for r in inferred.relationships] == [
        ("rel:loans.book_id", "books"),
        ("rel:loans.member_id", "members"),
    ]
    assert _tables(inferred)["books"].role == "entity"


def test_a_key_is_present_in_every_row_and_a_link_pair_too() -> None:
    gappy = _table("t", "a b", ("x", "p"), (None, "q"), ("y", "r"))
    assert _tables(infer([gappy]))["t"].key == ("b",)
    members = _table("members", "member_id", ("m1",), ("m2",))
    books = _table("books", "book_id", ("b1",), ("b2",))
    for rows, key in (
        ([("m1", "b1"), ("m2", "b1"), ("m1", "b2")], ("member_id", "book_id")),
        ([("m1", "b1"), ("m1", "b1"), ("m2", "b2")], None),
        ([("m1", "b1"), ("m2", None), ("m2", "b2"), ("m1", "b2")], None),
    ):
        links = _table("likes", "member_id book_id", *rows)
        assert _tables(infer([members, books, links]))["likes"].key == key
    shelves = _table("shelves", "shelf_id", ("s1",), ("s2",))
    rows = [("m1", "b1", "s1"), ("m2", "b1", "s2"), ("m1", "b2", "s2")]  # every pair is unique
    three = _table("likes", "member_id book_id shelf_id", *rows)
    assert _tables(infer([members, books, shelves, three]))["likes"].key == ("member_id", "book_id")


def test_a_table_without_a_key_is_noted() -> None:
    inferred = infer([_table("readings", "value", ("1",), ("1",))])
    assert _tables(inferred)["readings"].key is None
    assert _tables(inferred)["readings"].role == "measurement"
    assert [subject for subject, _ in _notes(inferred)] == ["readings"]


Importing = Any


def test_every_field_the_importer_sets_has_its_inference_and_no_status_is_asserted(
    roots: Any, importing: Importing
) -> None:
    directory = roots.inside / "lib"
    roots.write(
        "lib/books.csv",
        b"book_id,title,genre\nb1,Dune,sf\nb2,Emma,classic\nb3,Ubik,sf\nb4,Kim,classic\n",
    )
    roots.write(
        "lib/copies.csv", b"copy_id,book_id,bought\nc1,b1,2024-01-02\nc2,b1,NA\nc3,b2,2023-05-06\n"
    )
    imported = importing(directory)
    for descriptor in imported.built.descriptors:
        dumped = descriptor.model_dump(mode="json")
        for pointer, entry in descriptor.curation.items():
            assert entry.status != "asserted"
            assert entry.by == f"importer:aibi.files@{aibi.__version__}"
            field = pointer.removeprefix("/fields/")
            expected = dumped["label"] if pointer == "/label" else dumped["fields"][field]
            assert entry.inferred == expected
            for value in ("Dune", "Emma", "Ubik", "Kim", "b1", "c1", "2024-01-02"):
                assert value not in (entry.evidence or "")

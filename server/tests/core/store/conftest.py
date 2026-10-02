"""The store tests' fixture: a small lending library, imported from raw snapshots.

Members come from a CSV file (with missing codes, a list of interests and a derived age in
months); loans from a typed source, as a spreadsheet gives them (with a derived number of days
overdue); books from a TSV file. Loans reference their member and their book.

Test modules can't import one another (``--import-mode=importlib``), so the helpers are given as
fixtures, as in the engine tests.
"""

import itertools
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.schema.descriptors import Descriptor
from aibi.core.store.build import Layout
from aibi.core.store.sources import RawSource, SourceValue, TextSource, TypedSource
from aibi.core.store.store import Store

MEMBERS = (
    b"member_id,name,joined,age,interests\n"
    b"m-1,Ada,2020-01-02,36,poetry;maths\n"
    b"m-17,Grace,2021-03-04,NA,\n"
    b"m-3,Alan,2019-11-30,refused,maths;NA\n"
    b"m-4,Edsger,not yet,x,\n"
)
LOANS: tuple[tuple[SourceValue, ...], ...] = (
    (1, "m-1", "b-1", datetime(2024, 1, 2, 10, 0), 20.0),
    (2.0, "m-17", "b-2", datetime(2024, 2, 3, 11, 30), 3),
    (3, "m-17", "b-1", None, None),
    (4, "m-3", "b-2", "2024-03-04T05:06:07+02:00", float("nan")),
)
BOOKS = b"book_id\ttitle\tgenre\nb-1\tSICP\tcs\nb-2\tDune\tfiction\n"


def parse(**given: Any) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "format": "csv",
        "delimiter": ",",
        "quote": '"',
        "header_row": 0,
        "skip_rows": 0,
        "encoding": "utf-8",
    }
    settings.update(given)
    return settings


def library_descriptors(
    *,
    age_codes: Mapping[str, str] | None = None,
    members_parse: Mapping[str, Any] | None = None,
    extra: Sequence[Descriptor] = (),
) -> list[Descriptor]:
    table, column, relationship = build.table, build.column, build.relationship
    codes = (
        dict(age_codes) if age_codes is not None else {"NA": "UNKNOWN", "refused": "NOT_APPLICABLE"}
    )
    members_source: dict[str, Any] = {
        "kind": "file",
        "name": "members",
        "original_name": "members.csv",
    }
    members_source["parse"] = dict(members_parse) if members_parse is not None else parse()
    books_source = {
        "kind": "file",
        "name": "books",
        "original_name": "books.tsv",
        "parse": parse(format="tsv", delimiter="\t"),
    }
    loans_source = {"kind": "sheet", "name": "loans", "original_name": "Loans"}
    return [
        build.dataset(),
        table("members", ["member_id"], source=members_source),
        column("members.member_id", "string", identifier=True),
        column("members.name", "string"),
        column("members.joined", "date"),
        column("members.age", "integer", units="a", missing_codes=codes),
        column("members.interests", "list<category>", missing_codes={"NA": "UNKNOWN"}),
        column(
            "members.age_months",
            "number",
            derived={"op": "unit_convert", "input": "age", "units": "mo"},
        ),
        table("loans", ["loan_id"], role="event", source=loans_source),
        column("loans.loan_id", "integer"),
        column("loans.member_id", "string"),
        column("loans.book_id", "string"),
        column("loans.borrowed", "datetime"),
        column("loans.days", "number", units="d"),
        column(
            "loans.overdue",
            "number",
            derived={"op": "arith", "operator": "-", "args": ["days", 14]},
        ),
        table("books", ["book_id"], source=books_source),
        column("books.book_id", "string"),
        column("books.title", "string"),
        column("books.genre", "category"),
        relationship("loans", ["member_id"], "members", role="member"),
        relationship("loans", ["book_id"], "books", role="book"),
        *extra,
    ]


LAYOUTS = {
    "members": Layout(
        "members",
        tuple((name, name) for name in ("member_id", "name", "joined", "age", "interests")),
    ),
    "loans": Layout(
        "loans",
        (
            ("loan_id", "Loan"),
            ("member_id", "Member"),
            ("book_id", "Book"),
            ("borrowed", "Borrowed"),
            ("days", "Days"),
        ),
    ),
    "books": Layout("books", (("book_id", "book_id"), ("title", "title"), ("genre", "genre"))),
}


def library_sources(
    members: bytes = MEMBERS, loans: Sequence[tuple[SourceValue, ...]] = LOANS
) -> dict[str, RawSource]:
    return {
        "members": TextSource(members),
        "loans": TypedSource(("Loan", "Member", "Book", "Borrowed", "Days"), tuple(loans)),
        "books": TextSource(BOOKS),
    }


@dataclass
class Library:
    descriptors: Callable[..., list[Descriptor]] = library_descriptors
    sources: Callable[..., dict[str, RawSource]] = library_sources
    layouts: Mapping[str, Layout] = field(default_factory=lambda: LAYOUTS)
    members: bytes = MEMBERS
    loans: tuple[tuple[SourceValue, ...], ...] = LOANS


@pytest.fixture(scope="session")
def library() -> Library:
    return Library()


def _clock() -> Callable[[], datetime]:
    ticks = itertools.count()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return lambda: start + timedelta(seconds=next(ticks))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    opened = Store(tmp_path / "data", clock=_clock())
    yield opened
    opened.close()


@pytest.fixture
def imported(store: Store, library: Library) -> str:
    """The library imported and published as ``lib@1``; the manifest's hash."""
    with store.pin() as pin:
        built = store.import_release(
            pin, "lib", library.descriptors(), library.sources(), library.layouts
        )
        store.publish("lib", built.manifest.hash, "operator:ada")
    return built.manifest.hash

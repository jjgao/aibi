"""Golden cohort ids and cohort-count digests (§7.6, §13.4; D282, D283).

``golden/cohorts.json`` holds documents over a small lending library, each with the cohort id,
computation id, leaf keys and count digest they had when they were checked in; the digest is the
one ``count_cohort`` serves, taken after the disclosure pass (§7.6, D283, D297). The same id MUST
give the same digest: a change to either fails here unless a version in the id was bumped (the
semantics version, or a pack's results version), and then the file is written again.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.engine.canonical import Canonicalisation
from aibi.core.engine.counts import count_parts
from aibi.core.engine.data import Release
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.suppression import disclosed

GOLDEN = Path(__file__).parent / "golden" / "cohorts.json"
Canon = Callable[..., Canonicalisation]

LOANS = "rel:loans.member"
BOOKS = "rel:loans.book"


def library() -> Release:
    """Members borrow books; every member's loans are recorded, and a loan looks up its book."""
    column, table = build.column, build.table
    descriptors = [
        build.dataset(),
        table("members", ["member_id"]),
        column("members.member_id", "string", identifier=True),
        column("members.joined", "date"),
        column("members.age", "integer", units="a", missing_codes={"n/a": "NOT_APPLICABLE"}),
        column(
            "members.tier",
            "category",
            permissible_values={
                "values": [{"value": v} for v in ("bronze", "silver", "gold")],
                "ordered": True,
            },
        ),
        table("books", ["book_id"]),
        column("books.book_id", "string"),
        column("books.pages", "integer", units="1"),
        column("books.genre", "category"),
        table("loans", ["loan_id"], role="event"),
        column("loans.loan_id", "string"),
        column("loans.member_id", "string"),
        column("loans.book_id", "string"),
        column("loans.days", "number", units="d", missing_codes={"?": "NOT_ASSESSED"}),
        build.relationship("loans", ["member_id"], "members", role="member"),
        build.coverage(LOANS, "all"),
        build.relationship("loans", ["book_id"], "books", role="book"),
        build.coverage(BOOKS, "all"),
    ]
    rows: dict[str, list[dict[str, Any]]] = {
        "members": [
            {"member_id": "m1", "joined": "2020-03-01", "age": 34, "tier": "gold"},
            {"member_id": "m2", "joined": "2021-07-15", "age": "n/a", "tier": "bronze"},
            {"member_id": "m3", "age": 71},
            {"member_id": "m4", "joined": "2019-11-30", "tier": "silver"},
        ],
        "books": [
            {"book_id": "b1", "pages": 320, "genre": "history"},
            {"book_id": "b2", "pages": 90, "genre": "poetry"},
            {"book_id": "b3", "genre": "history"},
        ],
        "loans": [
            {"loan_id": "l1", "member_id": "m1", "book_id": "b1", "days": 12.5},
            {"loan_id": "l2", "member_id": "m1", "book_id": "b2", "days": "?"},
            {"loan_id": "l3", "member_id": "m2", "book_id": "b3", "days": 30},
            {"loan_id": "l4", "member_id": "m4", "book_id": "b2", "days": 3},
        ],
    }
    return build.release(descriptors, rows, dataset="library")


def _cases() -> dict[str, dict[str, Any]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(_cases()))
def test_golden_cohort_ids_and_count_digests_are_unchanged(canon: Canon, name: str) -> None:
    case = _cases()[name]
    release = library()
    result = canon(case["document"], {"library": release}, floor=case.get("floor"))
    assert result.refusals == []
    [cohort] = result.cohorts.values()
    found = {
        "id": cohort.id,
        "computation_id": cohort.computation_id,
        "keys": sorted(cohort.keys),
        "digest": disclosed(
            count_parts(cohort, evaluate(cohort.resolved)), cohort.identity.disclosure
        ).digest,
    }
    assert found == {member: case[member] for member in found}

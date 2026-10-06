"""Finishing refusals (SPEC §8.6, D405): ``finish_lazy`` returns what ``finish_refusals`` returns
over the refusals its records stand for, building only those it returns, and a list finished
twice (its paths rewritten in between, as ``by_id``, ``rooted`` and the gate do) ends in one
refusal that says how many were left out, with the exact count; finished lists joined with
others have their counts summed."""

import random
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.schema import refusals as refusals_module
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.limits import MAX_REFUSALS
from aibi.core.schema.output import DataSegment, TextSegment, data, text
from aibi.core.schema.refusals import (
    Limit,
    Record,
    Refusal,
    RefusalCode,
    built,
    finish_lazy,
    finish_refusals,
    left_out,
    said,
)
from aibi.core.schema.release import check_release

CODES: list[str] = [
    RefusalCode.INVALID_VALUE,
    RefusalCode.UNKNOWN_DESCRIPTOR,
    RefusalCode.LIMIT_EXCEEDED,
    "shelf.MARKED",
]


def _path(chooser: random.Random, width: int) -> str | None:
    pick = chooser.randrange(width + 2)
    if pick == 0:
        return None
    if pick == 1:
        return ""
    return f"/{pick - 2}/fields/{chooser.choice('ab')}"


def _records(chooser: random.Random, count: int, width: int) -> list[Record]:
    """``count`` records over about ``2 + 2 * width`` paths and four codes, each with its own
    message, so that the first of each (path, code) is told apart from the others."""
    return [
        (
            _path(chooser, width),
            chooser.choice(CODES),
            said,
            ("Refused, the ", f"{position}th", " time"),
        )
        for position in range(count)
    ]


def _distinct(count: int) -> list[Record]:
    """Exactly ``count`` distinct (path, code), each twice: each first in a shuffled order, then
    each again in another."""
    once: list[Record] = [
        (f"/{index}", CODES[index % 2], said, (f"first {index}",)) for index in range(count)
    ]
    twice: list[Record] = [(at, code, said, ("second",)) for at, code, _, _ in once]
    random.Random(count).shuffle(once)
    random.Random(count + 1).shuffle(twice)
    return [*once, *twice]


def dumped(found: list[Refusal]) -> list[dict[str, Any]]:
    return [refusal.model_dump(mode="json") for refusal in found]


def _same(records: list[Record]) -> list[Refusal]:
    lazy = finish_lazy(records)
    assert dumped(lazy) == dumped(finish_refusals([built(record) for record in records]))
    return lazy


@pytest.mark.parametrize("cap", [3, 7, MAX_REFUSALS])
def test_finishing_records_equals_finishing_their_refusals(
    cap: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(refusals_module, "MAX_REFUSALS", cap)
    chooser = random.Random(cap)
    for _ in range(60 if cap < MAX_REFUSALS else 6):
        width = chooser.choice([1, 3, cap, 2 * cap, 10 * cap])
        _same(_records(chooser, chooser.randrange(4 * cap + 2 * width), width))


@pytest.mark.parametrize("cap", [3, 7, MAX_REFUSALS])
def test_finishing_straddles_the_cap(cap: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exactly one fewer, as many and one more distinct (path, code) than the cap: the mutants
    ``<`` for ``<=``, one built too many, and a remainder off by one each fail one of them."""
    monkeypatch.setattr(refusals_module, "MAX_REFUSALS", cap)
    for count in (cap - 1, cap, cap + 1, 2 * cap + 5):
        found = _same(_distinct(count))
        assert len(found) == min(count, cap) + (count > cap)
        assert [str(refusal.code) for refusal in found].count("LIMIT_EXCEEDED") == (count > cap)
        if count > cap:
            assert dumped(found[-1:]) == dumped([left_out(count - cap)])
            assert found[-1].limit is not None
            assert found[-1].limit.max == cap
        shown = found[: min(count, cap)]
        assert all(r.message[0].model_dump()["text"].startswith("first") for r in shown)


def test_a_finished_list_finished_again_keeps_one_count_last() -> None:
    """``by_id``, ``rooted`` and the gate finish what was finished: the count the first finish
    gives is added to the second's, never kept as a refusal of its own (which sorted first and
    cut a real refusal)."""
    found = finish_refusals([built(record) for record in _distinct(MAX_REFUSALS + 500)])
    assert dumped(found[-1:]) == dumped([left_out(500)])
    rewritten = [
        refusal
        if refusal.path is None
        else refusal.model_copy(update={"path": "/draft" + refusal.path})
        for refusal in found
    ]
    again = finish_refusals(rewritten)
    assert len(again) == MAX_REFUSALS + 1
    assert dumped(again[-1:]) == dumped([left_out(500)])
    assert [r.path for r in again[:-1]] == [r.path for r in rewritten[:-1]]
    other = finish_refusals([_apart(built(record)) for record in _distinct(MAX_REFUSALS + 500)])
    joined = finish_refusals([*found, *other])  # two finished lists of distinct refusals
    assert len(joined) == MAX_REFUSALS + 1
    assert dumped(joined[-1:]) == dumped([left_out(2 * (MAX_REFUSALS + 500) - MAX_REFUSALS)])
    under = finish_refusals([*found[:10], found[-1]])
    assert dumped(under) == dumped([*found[:10], left_out(500)])


def _apart(refusal: Refusal) -> Refusal:
    """``refusal`` at a path no other of the tests' refusals has."""
    return refusal.model_copy(update={"path": "/apart" + (refusal.path or "/none")})


@pytest.mark.parametrize("cap", [3, 7, MAX_REFUSALS])
def test_finish_rewrite_finish_has_one_exact_count_last(
    cap: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A property over random lists and injective rewrites of their paths: at most one
    ``LIMIT_EXCEEDED`` of the refusals' limit, last, and the refusals shown plus those it counts
    are every distinct (path, code); a rewrite that keeps the order changes nothing else."""
    monkeypatch.setattr(refusals_module, "MAX_REFUSALS", cap)
    chooser = random.Random(cap + 1)
    for _ in range(40 if cap < MAX_REFUSALS else 4):
        width = chooser.choice([2, cap, 3 * cap])
        refusals = [built(r) for r in _records(chooser, chooser.randrange(6 * cap), width)]
        distinct = len({(r.path, str(r.code)) for r in refusals})
        names = sorted({r.path for r in refusals if r.path is not None})
        shuffled = names[:]
        chooser.shuffle(shuffled)
        keeping = {name: "/draft" + name if name else name for name in names}
        changing = {name: f"/z{shuffled.index(name):05d}" for name in names}
        for rewrite in (keeping, changing):
            again = finish_refusals(_moved(finish_refusals(refusals), rewrite))
            limits = [r for r in again if str(r.code) == "LIMIT_EXCEEDED" and r.limit is not None]
            assert len(limits) <= 1
            shown = len(again) - len(limits)
            if limits:
                assert again[-1] is limits[0]
                count = int(limits[0].message[0].model_dump()["text"].split()[0])
                assert shown + count == distinct
            else:
                assert shown == distinct
        fresh = [_apart(built(r)) for r in _records(chooser, chooser.randrange(6 * cap), width)]
        both = distinct + len({(r.path, str(r.code)) for r in fresh})
        joined = finish_refusals([*finish_refusals(refusals), *fresh])
        counts = [r for r in joined if r.limit is not None]
        said_ = sum(int(r.message[0].model_dump()["text"].split()[0]) for r in counts)
        assert len(joined) - len(counts) + said_ == both
        once = finish_refusals(_moved(refusals, keeping))
        assert dumped(finish_refusals(_moved(finish_refusals(refusals), keeping))) == dumped(once)


@pytest.mark.parametrize("cap", [3, 7, MAX_REFUSALS])
def test_a_finished_list_joined_with_fresh_refusals_over_the_cap_sums_the_counts(
    cap: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``cohorts`` joins finished lists and fresh refusals: one finished list with 1.5 caps
    of distinct refusals (a count of half a cap) and 1.2 caps of fresh ones, which on their
    own would leave 0.2 caps out, leave 1.7 caps out: the counts are added, not the larger
    taken."""
    monkeypatch.setattr(refusals_module, "MAX_REFUSALS", cap)
    earlier = finish_refusals([built(record) for record in _distinct(cap + cap // 2)])
    fresh = [_apart(built(record)) for record in _distinct((6 * cap) // 5)]
    joined = finish_refusals([*earlier, *fresh])
    total = (cap + cap // 2) + (6 * cap) // 5
    assert len(joined) == cap + 1
    assert dumped(joined[-1:]) == dumped([left_out(total - cap)])
    if cap == MAX_REFUSALS:
        assert dumped(joined[-1:]) == dumped([left_out(1700)])


def _numbered(first: int, end: int) -> list[Refusal]:
    return finish_refusals(
        [
            Refusal(code="INVALID_VALUE", path=f"/{index:05d}", message=[text("x")])
            for index in range(first, end)
        ]
    )


@pytest.mark.parametrize(
    ("second", "said_", "exact"),
    [
        ((1000, 2000), 1500, False),  # one the first left out, the second shows
        ((500, 2000), 1500, False),  # one the first left out, the second shows, and its own
        ((1000, 2500), 2000, False),  # the same, and a refusal each left out
        ((0, 1000), 500, True),  # both show what the first shows, none left out is held
        ((3000, 4500), 2000, True),  # each leaves its own out, none is held by the other
    ],
)
def test_a_join_counts_twice_a_refusal_one_list_left_out_and_another_holds(
    second: tuple[int, int], said_: int, exact: bool
) -> None:
    """The first list is 1,500 refusals, which leaves 1000..1499 out. What a join says was left
    out is an upper bound, exact when no list holds a refusal another left out: not only a
    refusal left out by both, but one the first left out and the second shows is counted twice."""
    first = _numbered(0, 1500)
    joined = finish_refusals([*first, *_numbered(*second)])
    distinct = len(set(range(1500)) | set(range(*second)))
    assert len(joined) == MAX_REFUSALS + 1
    assert dumped(joined[-1:]) == dumped([left_out(said_)])
    assert (said_ == distinct - MAX_REFUSALS) is exact
    assert said_ >= distinct - MAX_REFUSALS


def _moved(found: list[Refusal], rewrite: dict[str, str]) -> list[Refusal]:
    return [r if r.path is None else r.model_copy(update={"path": rewrite[r.path]}) for r in found]


def _like_the_count(**changes: Any) -> Refusal:
    return left_out(3).model_copy(update=changes)


LOOKALIKES: dict[str, Refusal] = {
    "a path": _like_the_count(path="/0"),
    "the root as its path": _like_the_count(path=""),
    "another limit's name": _like_the_count(limit=Limit(name="steps", max=MAX_REFUSALS)),
    "no limit": _like_the_count(limit=None),
    "another code": _like_the_count(code=RefusalCode.INVALID_VALUE),
    "a pack's code": _like_the_count(code="shelf.MARKED"),
    "a data segment": _like_the_count(message=[data("3 more refusals were left out")]),
    "two segments": _like_the_count(message=[text("3 more refusals were left out"), text("")]),
    "no segment": _like_the_count(message=[]),
    "a suffix": _like_the_count(message=[text("3 more refusals were left out of the index")]),
    "a prefix": _like_the_count(message=[text("Only 3 more refusals were left out")]),
    "a leading space": _like_the_count(message=[text(" 3 more refusals were left out")]),
    "no number": _like_the_count(message=[text("more refusals were left out")]),
    "another digit": _like_the_count(message=[text("\u0663 more refusals were left out")]),
}
"""Refusals that fail one guard of the fold each, and are the core's count but for it: a pack's
refusal, or one a user's text makes, must neither be taken for the count nor lose a refusal."""


@pytest.mark.parametrize("name", sorted(LOOKALIKES))
def test_a_refusal_like_the_count_but_not_the_core_s_is_kept(name: str) -> None:
    """Only the core's count is folded: what fails any one of its guards (path, code, limit, one
    text segment of exactly its words) stays a refusal, is not added to the count, and is
    shown beside the count of the others."""
    like = LOOKALIKES[name]
    assert dumped(finish_refusals([like])) == dumped([like])
    assert dumped(finish_refusals([like, left_out(5)])) == dumped([like, left_out(5)])
    shown = _distinct(2)[:2]
    mixed = finish_refusals([*(built(record) for record in shown), left_out(5), like])
    assert dumped(mixed[-1:]) == dumped([left_out(5)])
    assert dumped([like]) == dumped([r for r in mixed if r.message == like.message])


# --- Building only what is returned ---------------------------------------------------------


class _Counted:
    """Counts the refusals and the segments built while it is in place."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.refusals = 0
        self.segments = 0
        self._count(monkeypatch, Refusal, "refusals")
        self._count(monkeypatch, TextSegment, "segments")
        self._count(monkeypatch, DataSegment, "segments")

    def _count(self, monkeypatch: pytest.MonkeyPatch, model: Any, attribute: str) -> None:
        real = model.__init__

        def counting(this: Any, *args: Any, **kwargs: Any) -> None:
            setattr(self, attribute, getattr(self, attribute) + 1)
            real(this, *args, **kwargs)

        monkeypatch.setattr(model, "__init__", counting)


def _columns(count: int, concept: str) -> list[Descriptor]:
    column = build.column("members.c", "string", maps_to={"concept": concept, "transform": None})
    return [
        build.dataset(),
        build.table("members"),
        *(column.model_copy(update={"id": f"members.c{index:06d}"}) for index in range(count)),
    ]


def test_check_release_builds_only_the_refusals_it_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written = _columns(3 * MAX_REFUSALS, "core:nothing")
    counted = _Counted(monkeypatch)
    found = check_release(written)
    assert len(found) == MAX_REFUSALS + 1
    assert counted.refusals == MAX_REFUSALS + 1
    assert counted.segments <= 20 * (MAX_REFUSALS + 1)
    assert dumped(found[-1:]) == dumped([left_out(2 * MAX_REFUSALS)])

"""Compilers for tests that force how grouped coverage is listed, and check what the decision
counted against what the compiler built (SPEC D449), as a library: the engine's conftest gives
them to its tests as fixtures, and a test of another directory (the domain job's) imports them,
which a conftest is not for.

The compiler has no switch: a test forces a path by subclassing it and overriding ``decide``,
which every compile entry calls before it builds anything. ``main`` admits nothing; ``one_group``
admits every grouped coverage the parts list; ``auto`` is the compiler as the server runs it.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from aibi.core.engine import sql as compiler
from aibi.core.engine.resolve import ResolvedCohort, ResolvedVariable
from aibi.core.store.tables import TableSource

GroupedPath = Literal["auto", "main", "one_group"]
ENTRIES = ("compile", "crossing", "materialised", "members")
"""The compile entries that decide by the rule; the listing of inputs decides too, once, admitting
nothing (``admit(frozenset())``, D342, D449)."""


def forced_compiler(path: GroupedPath, given: type | None = None) -> type:
    """The compiler (or ``given``, a subclass of it) whose decision is ``path``'s."""
    base: type = given or compiler._Compiler  # pyright: ignore[reportPrivateUsage]
    if path == "auto":

        class Auto(base):  # type: ignore[misc, valid-type]
            pass

        return Auto

    class Forced(base):  # type: ignore[misc, valid-type]
        def decide(
            self, cohorts: Sequence[ResolvedCohort], variables: Sequence[ResolvedVariable] = ()
        ) -> None:
            listings = compiler._Listings(self).of(cohorts, variables)  # pyright: ignore[reportPrivateUsage]
            noted = getattr(self, "note", None)
            if noted is not None:
                noted(listings)
            self.admit(frozenset(listings) if path == "one_group" else frozenset())

    return Forced


def forced(
    path: GroupedPath, cohort: ResolvedCohort, sources: Mapping[str, TableSource], **keywords: Any
) -> Any:
    """A compiler of ``cohort`` taking ``path``."""
    return forced_compiler(path)(cohort, sources, **keywords)


def _grouped(coverage: Any) -> bool:
    return compiler._is_grouped(coverage)  # pyright: ignore[reportPrivateUsage]


def listings_checked(path: GroupedPath) -> type:
    """The compiler taking ``path``, which checks, at each compile entry (a cohort, a crossing, a
    materialisation, a members query):

    - that it decides exactly once, before it builds its first relation, and that what it
      admitted then is what it admits when the entry returns;
    - that every listing of a coverage it admitted is built by the one-group path, and none of a
      coverage it did not admit;
    - and, when the entry returns, that the listings of each grouped coverage its decision
      counted (``_Listings``) are the listings it built (D449)."""
    given: type = compiler._Compiler  # pyright: ignore[reportPrivateUsage]

    class Checked(given):  # type: ignore[misc, valid-type]
        def __init__(self, *arguments: Any, **keywords: Any) -> None:
            super().__init__(*arguments, **keywords)
            self.counted_listings: dict[str, int] | None = None
            self.built_listings: dict[str, set[object]] = {}
            self.decisions = 0
            self.admitted_at_first_build: frozenset[str] | None = None

        def admit(self, chosen: frozenset[str]) -> None:
            self.decisions += 1
            super().admit(chosen)

        def built(self) -> None:
            assert self.counted_listings is not None, "a relation was built before the decision"
            if self.admitted_at_first_build is None:
                self.admitted_at_first_build = self.admitted

        def note(self, listings: dict[str, int]) -> None:
            self.counted_listings = listings

        def decide(self, cohorts: Any, variables: Any = ()) -> None:
            self.note(compiler._Listings(self).of(cohorts, variables))  # pyright: ignore[reportPrivateUsage]
            super().decide(cohorts, variables)

        def inputs(self, *arguments: Any, **keywords: Any) -> Any:
            # The listing of inputs admits nothing (D342, D449): it builds main's SQL whatever
            # the sizes, so it holds nothing to count.
            self.counted_listings = {}
            found = super().inputs(*arguments, **keywords)
            assert self.decisions == 1, ("inputs", self.decisions)
            assert self.admitted == frozenset()
            assert self.admitted_at_first_build == frozenset()
            return found

        def cte(self, select: Any) -> str:
            self.built()
            return super().cte(select)

        def base(self, table: str) -> Any:
            self.built()
            return super().base(table)

        def listing(self, coverage: Any, columns: Any, admitted: Any) -> str:
            found = super().listing(coverage, columns, admitted)
            if _grouped(coverage):
                key = compiler._admitted_key(columns, admitted)  # pyright: ignore[reportPrivateUsage]
                self.built_listings.setdefault(coverage.relationship, set()).add(key)
                one_group = ("one group", coverage.relationship, key) in self.memo
                assert one_group == (coverage.relationship in self.admitted), (
                    "a listing took the path its coverage was not decided for",
                    coverage.relationship,
                )
            return found

        def scope_values(self, coverage: Any, column: Any, kind: Any) -> str | None:
            found = super().scope_values(coverage, column, kind)
            if found is not None and _grouped(coverage):
                self.built_listings.setdefault(coverage.relationship, set()).add(("v", found))
            return found

    def checked(entry: str) -> Callable[..., Any]:
        def run(self: Any, *arguments: Any, **keywords: Any) -> Any:
            found = getattr(given, entry)(self, *arguments, **keywords)
            built = {name: len(keys) for name, keys in sorted(self.built_listings.items())}
            assert self.counted_listings == built, (entry, self.counted_listings, built)
            assert self.decisions == 1, (entry, "decisions", self.decisions)
            assert self.admitted == self.admitted_at_first_build, (
                entry,
                "the decision changed while the entry built",
            )
            return found

        return run

    for entry in ENTRIES:
        setattr(Checked, entry, checked(entry))
    return forced_compiler(path, Checked)

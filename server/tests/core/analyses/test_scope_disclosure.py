"""What ``compare.existence`` shows of its predicates under *k*, by scope (SPEC §8.4; D424).

A view's predicates are disclosed by **scope** (``disclosure.scopes``): a position alone where
the view refuses overlap, every position where it allows it. A predicate alone keeps D320's rule
(``test_disclosure``); every other scope is gated, shown whole or not at all, and never shows
its maps of reasons nor its joint counts under *k* (``existence.never_shown``).

The brute force runs ``existence.compare`` whole over **worlds**: a model lists unit **types**,
each a unit's answer for every cohort of the view (``T``, ``F``, or UNKNOWN for a reason, with
the top-level clauses UNKNOWN for it) and for every predicate (``T``, ``F``, UNKNOWN for a
reason, lower case where the answer is flagged), and a world is a multiset of a few units over
the types. Types are joint answers, so a model states how its predicates depend on one another
and on the cohorts (a predicate and its ``not``, ``known``, ``unknown``, a union of categories,
a category no unit holds, which gives each unit's default, a predicate under the other lift
rule, a cohort that reads the predicate's rows). For each world the **secrets** are the members
that differ between the output without *k* and with it, of the scope's gate (every predicate's
counts at its positions) and of its own positions' populations; members never shown under *k*
for the scope's shape are not secrets. One oracle: a secret **leaks** where the outputs that
read the same (population, ``analysed``, values and caveats), over worlds whose types lie within
the world's own, give it one value from 1 to *k* − 1, and the populations alone, over the same
worlds, do not (D424's claim, for *k* of 3 or more; at *k* = 2 only a position alone is held to
it). Where a view refusing overlap has two positions, a position's population can be given by
another scope (V2, #112), so its members are secrets only in the position's own view.
"""

import itertools
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from aibi.core.analyses.disclosure import Cohort, Split
from aibi.core.analyses.existence import (
    ANALYSED_MAP,
    ANALYSED_N,
    ANALYSED_UNITS,
    DENOMINATOR,
    LIFT,
    MAP,
    NUMERATOR,
    VARIABLE_MAP,
    VARIABLE_N,
    VARIABLE_UNITS,
    Outcome,
    disclosed_members,
    never_shown,
)
from aibi.core.analyses.views import CheckedView
from aibi.core.schema.caveats import CaveatCode
from aibi.core.schema.semantics import ExclusionReason

World = Callable[..., Outcome]
Viewed = Callable[..., CheckedView]
Type = tuple[tuple[str, ...], tuple[str, ...]]
"""A unit's answer for each cohort and for each predicate of a view."""
TEMPLATES = (
    NUMERATOR,
    DENOMINATOR,
    MAP,
    LIFT,
    ANALYSED_N,
    ANALYSED_UNITS,
    ANALYSED_MAP,
    VARIABLE_N,
    VARIABLE_UNITS,
    VARIABLE_MAP,
)
MAPS = (MAP, VARIABLE_MAP, ANALYSED_MAP)


def small(k: int, count: int) -> bool:
    """A count from 1 to *k* − 1 (the test's own, so a mutant of the pass leaves it)."""
    return 1 <= count <= k - 1


@dataclass(frozen=True)
class Model:
    """Unit types over a view's cohorts and predicates, with whether the view allows overlap
    and how many top-level clauses its cohorts have."""

    name: str
    types: tuple[Type, ...]
    allow: bool
    clauses: int = 1

    @property
    def positions(self) -> int:
        return len(self.types[0][0])

    @property
    def predicates(self) -> int:
        return len(self.types[0][1])

    def scope(self, position: int) -> tuple[int, ...]:
        return tuple(range(self.positions)) if self.allow else (position,)


def _model(name: str, types: Sequence[Type], *, allow: bool, clauses: int = 1) -> Model:
    return Model(name, tuple(dict.fromkeys(types)), allow, clauses)


# --- Worlds ---------------------------------------------------------------------------------


def _multisets(size: int, slots: int) -> Iterator[tuple[int, ...]]:
    for combination in itertools.combinations_with_replacement(range(slots), size):
        counts = [0] * slots
        for slot in combination:
            counts[slot] += 1
        yield tuple(counts)


def _leaves(answer: str) -> frozenset[int]:
    digits = frozenset(int(c) for c in answer[1:])
    return digits or frozenset({0})


def _signature(model: Model, world: tuple[int, ...]) -> tuple[Any, ...]:
    """What ``compare`` reads of a world: each cohort's accounting (its units by answer, by
    reason and by clause), each predicate's split of its units with their reasons and whether
    one is flagged, the units for which no predicate is known by reason, and the units each
    pair of cohorts shares; worlds that agree on it give the same output."""
    found: list[Any] = []
    for at in range(model.positions):
        cohort: Counter[str] = Counter()
        clauses: Counter[int] = Counter()
        splits = [Counter[str]() for _ in range(model.predicates)]
        none: Counter[str] = Counter()
        for (cohorts, predicates), count in zip(model.types, world, strict=True):
            if not count:
                continue
            answer = cohorts[at]
            cohort[answer[0]] += count
            if answer not in ("T", "F"):
                for leaf in _leaves(answer):
                    clauses[leaf] += count
            if answer != "T":
                continue
            for split, given in zip(splits, predicates, strict=True):
                split[given.upper()] += count
                split["flagged"] |= given.islower()
            if all(given.upper() not in ("T", "F") for given in predicates):
                none["units"] += count
                for reason in {given.upper() for given in predicates}:
                    none[reason] += count
        found.append(
            (
                tuple(sorted(cohort.items())),
                tuple(sorted(clauses.items())),
                tuple(tuple(sorted(split.items())) for split in splits),
                tuple(sorted(none.items())),
            )
        )
    shared = tuple(
        sum(
            count
            for (cohorts, _), count in zip(model.types, world, strict=True)
            if cohorts[first] == cohorts[second] == "T"
        )
        for first, second in itertools.combinations(range(model.positions), 2)
    )
    return (tuple(found), shared)


def _patterns(model: Model, world: tuple[int, ...]) -> tuple[list[str], list[str], list[Any]]:
    units = [kind for kind, count in zip(model.types, world, strict=True) for _ in range(count)]
    cohorts = ["".join(unit[0][at][0] for unit in units) for at in range(model.positions)]
    predicates = ["".join(unit[1][j] for unit in units) for j in range(model.predicates)]
    leaves = [
        [frozenset[int]() if unit[0][at] in "TF" else _leaves(unit[0][at]) for unit in units]
        for at in range(model.positions)
    ]
    return cohorts, predicates, leaves


def _flat(outcome: Outcome) -> dict[str, Any]:
    """Every count of an output's population, ``analysed`` and predicates' values, by path; a
    map ``null`` under its own path, a shown one by each count."""
    found: dict[str, Any] = {}

    def put(path: str, value: Any) -> None:
        if isinstance(value, Mapping):
            for key, count in cast(Mapping[str, int], value).items():
                found[f"{path}/{key}"] = count
        else:
            found[path] = value

    for at, population in enumerate(outcome.population):
        for name in ("n_true", "n_false", "n_unknown", "unknown_by_reason", "unknown_by_leaf"):
            put(f"/population/{at}/{name}", getattr(population, name))
        put(f"/population/{at}/lift_differs", population.lift_differs)
    for at, analysed in enumerate(outcome.analysed):
        for name in ("n", "excluded_units", "excluded"):
            put(f"/analysed/{at}/{name}", getattr(analysed, name))
        for j, variable in enumerate(analysed.variables or ()):
            for name in ("n", "excluded_units", "excluded"):
                put(f"/analysed/{at}/variables/{j}/{name}", getattr(variable, name))
    for at, position in enumerate(outcome.values.positions):
        for j, share in enumerate(position.predicates):
            base = f"/values/positions/{at}/predicates/{j}"
            proportion = share.proportion
            put(f"{base}/proportion/numerator", proportion.numerator)
            put(f"{base}/proportion/denominator", proportion.denominator)
            put(f"{base}/proportion/excluded", proportion.excluded)
            put(f"{base}/lift_differs", share.lift_differs)
    return found


def _whole(outcome: Outcome) -> str:
    """The whole output, as ``test_disclosure._whole`` reads it."""
    return "|".join(
        [
            *(p.model_dump_json() for p in outcome.population),
            *(a.model_dump_json() for a in outcome.analysed),
            outcome.values.model_dump_json(),
            *(c.model_dump_json() for c in outcome.caveats),
        ]
    )


def _populations(outcome: Outcome) -> str:
    return "|".join(p.model_dump_json() for p in outcome.population)


REASONS = frozenset(reason.value for reason in ExclusionReason)


def template(path: str) -> str:
    """A member's path with its position and predicate as ``*``, a map's count as its map."""
    parts = path.split("/")
    if parts[-1] in REASONS:
        parts = parts[:-1]
    return "/".join("*" if part.isdigit() else part for part in parts)


def _position(path: str) -> int:
    parts = path.split("/")
    return int(parts[3] if parts[1] == "values" else parts[2])


def _value(flat: Mapping[str, Any], path: str) -> Any:
    """A path's value in a flattened output, ``None`` where it or a map holding it is."""
    if path in flat:
        return flat[path]
    parent = path.rsplit("/", 1)[0]
    return flat.get(parent)


@dataclass
class Run:
    """What a model's worlds of one size gave under one *k*: the secrets leaked, the counts
    shown from 1 to *k* − 1, by member template whether some world nulled it and whether some
    world showed it (a map shows it only with a count that is not 0: a map of zeros beside a
    shown 0 is given by that count), and the outputs whose nulls are not ``disclosed_members``'
    passes."""

    leaks: list[Any] = field(default_factory=list[Any])
    small_shown: list[Any] = field(default_factory=list[Any])
    nulled: set[str] = field(default_factory=set[str])
    shown: set[str] = field(default_factory=set[str])
    unmatched: list[Any] = field(default_factory=list[Any])
    exposed: list[Any] = field(default_factory=list[Any])

    def merge(self, other: "Run") -> None:
        self.leaks += other.leaks
        self.small_shown += other.small_shown
        self.nulled |= other.nulled
        self.shown |= other.shown
        self.unmatched += other.unmatched
        self.exposed += other.exposed


GATED_NEVER = frozenset({LIFT, MAP, VARIABLE_MAP, ANALYSED_N, ANALYSED_UNITS, ANALYSED_MAP})
"""What a gated scope never shows under *k*, written out here rather than read from the code
(``never_shown``): both paths of every map, ``analysed``'s counts and every ``lift_differs``;
a map of zeros beside a shown 0 aside."""


@dataclass(frozen=True)
class _Read:
    whole: str
    populations: str
    flat: dict[str, Any]
    unmatched: tuple[str, ...]
    flagged: bool
    """Whether a caveat names a flag of the predicates' answers (``/values``)."""


FLAGS = (CaveatCode.COVERAGE_PROPOSED, CaveatCode.SCOPE_PARTIAL)


class Worlds:
    """A model's worlds run through ``compare``, each reading once per *k* (``_signature``)."""

    def __init__(self, model: Model, viewed: Viewed, worlded: World) -> None:
        self.model = model
        self.view = viewed(
            model.positions, model.predicates, k=3, overlap=model.allow, clauses=model.clauses
        )
        self.worlded = worlded
        self.read: dict[tuple[Any, int | None], _Read] = {}

    def outcome(self, world: tuple[int, ...], k: int | None) -> Outcome:
        cohorts, predicates, leaves = _patterns(self.model, world)
        return self.worlded(self.view, cohorts, predicates, k=k, leaves=leaves)

    def reading(self, world: tuple[int, ...], k: int | None) -> _Read:
        key = (_signature(self.model, world), k)
        found = self.read.get(key)
        if found is None:
            outcome = self.outcome(world, k)
            flat = _flat(outcome)
            unmatched = () if k is None else self._unmatched(world, outcome, flat, k)
            flagged = any(c.code in FLAGS and "/values" in c.affects for c in outcome.caveats)
            found = _Read(_whole(outcome), _populations(outcome), flat, unmatched, flagged)
            self.read[key] = found
        return found

    def _unmatched(
        self, world: tuple[int, ...], outcome: Outcome, flat: Mapping[str, Any], k: int
    ) -> tuple[str, ...]:
        """The members whose null in the output is not what ``disclosed_members`` says of the
        same counts, and the counts nulled that it does not list."""
        off = self.reading(world, None).flat
        cohorts: list[Cohort] = []
        for at, population in enumerate(outcome.population):
            splits: list[Split] = []
            reasons: list[tuple[int, ...]] = []
            for j in range(self.model.predicates):
                base = f"/values/positions/{at}/predicates/{j}/proportion"
                true, known = off[f"{base}/numerator"], off[f"{base}/denominator"]
                size = off[f"/population/{at}/n_true"]
                splits.append(Split(true, known - true, size - known))
                reasons.append(tuple(off[f"{base}/excluded/{r.value}"] for r in ExclusionReason))
            cohorts.append(
                Cohort(
                    population.n_true is not None,
                    self._whole_shown(off, flat, at),
                    tuple(splits),
                    tuple(reasons),
                )
            )
        _, members = disclosed_members(cohorts, self.model.predicates, allow=self.model.allow, k=k)
        found = [
            path for path, by in members.items() if (flat.get(path, 0) is None) != (by is not None)
        ]
        found += [
            path
            for path, value in flat.items()
            if value is None
            and not path.startswith("/population")
            and path not in members
            and path.rsplit("/", 1)[0] not in members
        ]
        return tuple(found)

    @staticmethod
    def _whole_shown(off: Mapping[str, Any], flat: Mapping[str, Any], at: int) -> bool:
        """Whether nothing of a position's population is hidden (``lift_differs`` aside, which
        always is): every member of it that the output without *k* has, the output under *k*
        has, a map holding its counts or not."""
        base = f"/population/{at}/"
        return all(
            _value(flat, path) is not None
            for path, value in off.items()
            if path.startswith(base) and not path.endswith("/lift_differs") and value is not None
        )

    def run(self, size: int, k: int, *, populations: bool) -> Run:
        """Every world of ``size`` units under ``k`` (module docstring); ``populations`` makes
        the positions' population members secrets."""
        found = Run()
        groups: dict[str, dict[int, dict[str, set[int]]]] = defaultdict(dict)
        bases: dict[str, dict[int, dict[str, set[int]]]] = defaultdict(dict)
        worlds: list[tuple[int, _Read, dict[str, int]]] = []
        for world in _multisets(size, len(self.model.types)):
            mask = sum(1 << i for i, count in enumerate(world) if count)
            on, off = self.reading(world, k), self.reading(world, None)
            if on.unmatched:
                found.unmatched.append((world, k, on.unmatched))
            secrets = self._secrets(on, off, populations, found)
            found.small_shown += [
                (world, path, value)
                for path, value in on.flat.items()
                if isinstance(value, int) and small(k, value)
            ]
            group = groups[on.whole].setdefault(mask, defaultdict(set))
            for path, value in secrets.items():
                group[path].add(value)
            base = bases[on.populations].setdefault(mask, defaultdict(set))
            for path, value in off.flat.items():
                base[path].add(value)
            worlds.append((mask, on, secrets))
            if on.flagged != self._flag_expected(world, on):
                found.exposed.append((world, k, "a flag differs from the shown predicates'"))
            found.exposed += [
                (world, k, path)
                for path, value in on.flat.items()
                if not path.startswith("/population")
                and template(path) in (GATED_NEVER if self._gated(path) else {LIFT})
                and value is not None
                and (template(path) not in MAPS or value != 0)
            ]
        for mask, on, secrets in worlds:
            within = [given for m, given in groups[on.whole].items() if m & ~mask == 0]
            alone = [given for m, given in bases[on.populations].items() if m & ~mask == 0]
            for path, value in secrets.items():
                if not small(k, value):
                    continue
                if len(set[int]().union(*(given[path] for given in within))) > 1:
                    continue
                if len(set[int]().union(*(given[path] for given in alone))) > 1:
                    found.leaks.append((size, k, path, value, on.whole))
        return found

    def _flag_expected(self, world: tuple[int, ...], on: _Read) -> bool:
        """Whether a caveat names a flag of the predicates' answers (D424), predicate by
        predicate: one the position's units carry (a lower-case answer) and whose numerator
        **and** denominator are both shown at it; a predicate with either ``null`` names none."""
        for at, j in itertools.product(range(self.model.positions), range(self.model.predicates)):
            base = f"/values/positions/{at}/predicates/{j}/proportion"
            if on.flat[f"{base}/numerator"] is None or on.flat[f"{base}/denominator"] is None:
                continue
            if any(
                count and cohorts[at] == "T" and predicates[j].islower()
                for (cohorts, predicates), count in zip(self.model.types, world, strict=True)
            ):
                return True
        return False

    def _gated(self, path: str) -> bool:
        """Whether the scope of the position a member's path names is gated: written out, one
        predicate at one position being the only scope that is not (``disclosure.gated``)."""
        return len(self.model.scope(_position(path))) > 1 or self.model.predicates > 1

    def _secrets(self, on: _Read, off: _Read, populations: bool, found: Run) -> dict[str, int]:
        """The members that differ between the output without *k* and with it, but those the
        scope's shape never shows, and population members unless ``populations``."""
        secrets: dict[str, int] = {}
        model = self.model
        for path, value in off.flat.items():
            if not isinstance(value, int) or isinstance(value, bool):
                continue
            shown = _value(on.flat, path)
            if path.startswith("/population"):
                if shown is None and populations:
                    secrets[path] = value
                continue
            kind = template(path)
            scope = model.scope(_position(path))
            if shown is None:
                found.nulled.add(kind)
                if kind not in never_shown(len(scope), model.predicates):
                    secrets[path] = value
            elif kind not in MAPS or value:
                found.shown.add(kind)
        return secrets


# --- Models -----------------------------------------------------------------------------------

OUTSIDE = (("F",), ("A",))
"""A position's cohort answers for units outside it: a position is a proper subset of the
table."""


def _within(
    answers: Iterator[tuple[str, ...]] | Sequence[tuple[str, ...]],
    *,
    outside: Sequence[tuple[str, ...]] = OUTSIDE,
    allow: bool = False,
    name: str,
    clauses: int = 1,
) -> Model:
    """One position: units outside it as ``outside``, inside it answering ``answers``."""
    found = list(answers)
    width = len(found[0])
    types = [(cohort, ("F",) * width) for cohort in outside]
    types += [(("T",), given) for given in found]
    return _model(name, types, allow=allow, clauses=clauses)


def _kleene_or(first: str, second: str) -> str:
    if "T" in (first, second):
        return "T"
    return "F" if first == second == "F" else "A"


def _known(answer: str) -> str:
    return "T" if answer in "TF" else "F"


def _not(answer: str) -> str:
    return {"T": "F", "F": "T"}.get(answer, answer)


DEFAULTS = ("F", "A", "I")
"""A unit's answer for a category none of its rows holds (D381)."""


def refuse_two() -> Model:
    """A position X, a proper subset of the table, and two predicates over its units."""
    pairs = itertools.product("TFAI", "TFA")
    return _within(pairs, name="refuse X: P (TFAI), Q (TFA)")


def refuse_leaf() -> Model:
    """X of two top-level clauses, its UNKNOWN units by clause, and two predicates."""
    outside = (("F",), ("A0",), ("A1",), ("A01",))
    pairs = itertools.product("TFA", "TFA")
    return _within(pairs, outside=outside, clauses=2, name="refuse X of two clauses: P, Q")


def kleene() -> Model:
    """Every unit; P, Q and ``P or Q`` (round 2's joint world): three dependent predicates."""
    return _within(
        [(p, q, _kleene_or(p, q)) for p, q in itertools.product("TFA", "TFA")],
        outside=(),
        name="every unit: P, Q, P or Q",
    )


def coverage() -> Model:
    """Round 1's coverage world: P is ``group in [in]``, E a category no row holds of rows
    covered within that scope: FALSE or not assessed where P is TRUE, out of scope where it is
    FALSE, not assessed where it is UNKNOWN; E's OUT_OF_SCOPE units are P's FALSE ones."""
    return _within(
        [("T", "F"), ("T", "A"), ("F", "O"), ("A", "A")], outside=(), name="coverage, P and E"
    )


def coverage_at_x() -> Model:
    return _within(
        [("T", "F"), ("T", "A"), ("F", "O"), ("A", "A")], name="coverage at a position X"
    )


def two_reasons() -> Model:
    """Two predicates UNKNOWN for two reasons, their maps dependent."""
    pairs = [("T", "T"), ("F", "F"), ("A", "A"), ("A", "I"), ("I", "A"), ("I", "I")]
    return _within(pairs, outside=(), name="two dependent two-reason predicates")


def probe() -> Model:
    """``in [a]`` beside a category no unit holds (D383's example (a)): the probe answers
    each unit's default, which ``in [a]`` answers where the unit holds no a."""
    pairs = [("T", d) for d in DEFAULTS] + [(d, d) for d in DEFAULTS]
    return _within(pairs, name="refuse X: in [a] and a default probe")


def chain() -> Model:
    """A chain: P is ``in [a]``, Q ``in [a, b]`` (one column, a union), R ``known Q``; units
    holding none, a, b or both, each with a default."""
    found: set[tuple[str, ...]] = set()
    for holds, default in itertools.product(("", "a", "b", "ab"), DEFAULTS):
        p = "T" if "a" in holds else default
        q = "T" if holds else default
        found.add((p, q, _known(q)))
    return _within(sorted(found), name="refuse X: in [a], in [a, b], known in [a, b]")


def wrappers() -> Model:
    """P beside ``not P``, ``known P`` and ``unknown P``."""
    return _within(
        [(p, _not(p), _known(p), "F" if p in "TF" else "T") for p in "TFAI"],
        name="refuse X: P, not P, known P, unknown P",
    )


def lifted() -> Model:
    """P beside itself under the other lift rule, which may answer a unit P leaves UNKNOWN."""
    pairs = [("T", "T"), ("F", "F")] + [("A", lift) for lift in "TFA"]
    return _within(pairs, name="refuse X: P and P lifted")


def flagged() -> Model:
    """Answers flagged by proposed coverage (lower case): P's TRUE units, flagged or not."""
    pairs = itertools.product("TtFA", "TF")
    return _within(pairs, name="refuse X: P (flagged), Q")


def clauses_two() -> Model:
    """X of two top-level clauses under allow beside every unit: X's UNKNOWN units by clause
    are population members of the scope (P's alphabet is TF, so that worlds of 6 to 9 units
    are all enumerated)."""
    types = [(("T", x), (p,)) for x, p in itertools.product(("T", "F", "A0", "A1"), "TF")]
    return _model("allow every unit and X (two clauses): P (TF)", types, allow=True, clauses=2)


def allow_reasons() -> Model:
    """Every unit and X, overlap allowed, X UNKNOWN for two reasons (``A`` and ``I``), P TRUE or
    FALSE: a unit's UNKNOWN for X is a population member of the scope, by reason."""
    types = [(("T", x), (p,)) for x, p in itertools.product("TFAI", "TF")]
    return _model("allow every unit and X (TFAI): P (TF)", types, allow=True)


def allow_probe() -> Model:
    """Every unit and X, overlap allowed, one predicate that reads X's rows (round 1's default
    probe, the fixture of r4's m3): P is FALSE or UNKNOWN where X is TRUE, and UNKNOWN where X
    is not."""
    types = [(("T", "T"), ("F",)), (("T", "T"), ("A",)), (("T", "F"), ("F",)), (("T", "A"), ("A",))]
    return _model("allow every unit and X: P reads X's rows", types, allow=True)


def allow_one() -> Model:
    """Every unit and X, overlap allowed, one predicate: one predicate at two positions."""
    types = [(("T", x), (p,)) for x, p in itertools.product("TFA", "TFA")]
    return _model("allow every unit and X: P", types, allow=True)


def allow_three_positions() -> Model:
    """Every unit, X and ``not X``, overlap allowed, one predicate."""
    types = [(("T", x, _not(x)), (p,)) for x, p in itertools.product("TFA", "TFA")]
    return _model("allow every unit, X, not X: P", types, allow=True)


def allow_two() -> Model:
    """Every unit and X, overlap allowed, P and Q."""
    types = [(("T", x), (p, q)) for x, p, q in itertools.product("TFA", "TA", "TF")]
    return _model("allow every unit and X: P (TA), Q (TF)", types, allow=True)


def allow_three() -> Model:
    """Every unit and X, overlap allowed, three predicates."""
    types = [(("T", x), (p, q, r)) for x, p, q, r in itertools.product("TF", "TA", "TF", "TF")]
    return _model("allow every unit and X: P (TA), Q, R (TF)", types, allow=True)


def refuse_unknown_x() -> Model:
    """X and ``unknown X``, overlap refused: two scopes; X of two clauses."""
    types = [(("F", "F"), ("F", "F"))]
    types += [(("T", "F"), pair) for pair in itertools.product("TF", "TF")]
    types += [
        ((leaf, "T"), pair) for leaf in ("A0", "A1") for pair in itertools.product("TF", "TF")
    ]
    return _model("refuse X and unknown X: P, Q", types, allow=False, clauses=2)


def refuse_two_positions_three() -> Model:
    """X and ``unknown X``, overlap refused, three predicates."""
    types = [(("F", "F"), ("F", "F", "F"))]
    types += [(("T", "F"), trio) for trio in itertools.product("TF", "TA", "TF")]
    types += [(("A", "T"), (p, "T", r)) for p, r in itertools.product("TF", "TF")]
    return _model("refuse X and unknown X: P, Q, R", types, allow=False)


def alone_at(model: Model, position: int) -> Model:
    """A model's types seen by one of its positions, as a view of that position alone."""
    types = [((cohorts[position],), predicates) for cohorts, predicates in model.types]
    return _model(
        f"{model.name} (position {position} alone)", types, allow=False, clauses=model.clauses
    )


def refuse_three_positions() -> Model:
    """Three disjoint positions of a view that refuses overlap, one predicate at each: three
    scopes, each a predicate alone, none gated (``Worlds._gated``)."""
    types: list[Type] = [(("F", "F", "F"), ("F",))]
    for at in range(3):
        for answer in "TFA":
            types.append((tuple("T" if i == at else "F" for i in range(3)), (answer,)))
    return _model("refuse three positions: P alone", types, allow=False)


def alone() -> Model:
    """One predicate at a position X: a predicate alone, D320's rule."""
    return _within([(p,) for p in "TFAI"], name="refuse X: P alone")


def flagged_alone() -> Model:
    """A predicate alone with flagged TRUE units (lower case): D320 shows it with its numerator
    hidden beside a shown denominator, or the reverse, and D424 names no flag then."""
    return _within([(p,) for p in "tTFAI"], name="refuse X: P alone (flagged)")


Plan = tuple[tuple[int, tuple[int, ...]], ...]
CASES: dict[str, tuple[Callable[[], Model], Plan]] = {
    "alone": (alone, ((6, (2, 3)),)),
    "flagged_alone": (flagged_alone, ((6, (2, 3)),)),
    "refuse_two": (refuse_two, ((4, (2, 3)),)),
    "refuse_leaf": (refuse_leaf, ((4, (2, 3)),)),
    "kleene": (kleene, ((5, (2, 3)),)),
    "coverage": (coverage, ((7, (2, 3, 4, 5)), (8, (3, 4)), (9, (3, 4)))),
    "coverage_at_x": (coverage_at_x, ((6, (2, 3)), (7, (3,)))),
    "two_reasons": (two_reasons, ((6, (2, 3)), (7, (3,)))),
    "probe": (probe, ((5, (2, 3)),)),
    "chain": (chain, ((5, (2, 3)),)),
    "wrappers": (wrappers, ((6, (2, 3)), (7, (3,)))),
    "lifted": (lifted, ((6, (2, 3)),)),
    "flagged": (flagged, ((5, (2, 3)),)),
    "clauses_two": (clauses_two, ((6, (3, 4)), (7, (3,)), (8, (3,)), (9, (3,)))),
    "allow_reasons": (allow_reasons, ((6, (3, 4)), (7, (3,)), (8, (3,)), (9, (3,)))),
    "allow_probe": (
        allow_probe,
        ((6, (3,)), (7, (3,)), (8, (3,)), (9, (3,)), (10, (3,)), (11, (3,))),
    ),
    "allow_one": (allow_one, ((6, (3, 4)),)),
    "allow_three_positions": (allow_three_positions, ((6, (3,)),)),
    "allow_two": (allow_two, ((5, (3,)),)),
    "allow_three": (allow_three, ((4, (3,)),)),
    "refuse_three_positions": (refuse_three_positions, ((5, (3,)),)),
    "refuse_unknown_x": (refuse_unknown_x, ((4, (3,)),)),
    "refuse_unknown_x_0": (lambda: alone_at(refuse_unknown_x(), 0), ((4, (3,)), (5, (3,)))),
    "refuse_unknown_x_1": (lambda: alone_at(refuse_unknown_x(), 1), ((4, (3,)), (5, (3,)))),
    "refuse_three": (refuse_two_positions_three, ((4, (3,)),)),
}
"""Each model and the sizes of its worlds, each under the *k* it is run at: the claim is held
for *k* of 3 or more everywhere, and at *k* = 2 only by a view of one position."""
_RAN: dict[str, tuple[Model, Run]] = {}


def ran(name: str, viewed: Viewed, worlded: World) -> tuple[Model, Run]:
    """A case's model and its worlds' run, once per session."""
    found = _RAN.get(name)
    if found is None:
        make, plan = CASES[name]
        model = make()
        assert model.positions == 1 or all(k >= 3 for _, ks in plan for k in ks), name
        worlds = Worlds(model, viewed, worlded)
        merged = Run()
        populations = model.allow or model.positions == 1
        for size, ks in plan:
            for k in ks:
                merged.merge(worlds.run(size, k, populations=populations))
        found = _RAN[name] = (model, merged)
    return found


@pytest.mark.parametrize("name", CASES)
def test_no_count_of_a_scope_or_of_its_positions_populations_is_pinned(
    viewed: Viewed, worlded: World, name: str
) -> None:
    """Over every world of the case's model, no member of a scope's gate or of its own
    positions' populations is given a value from 1 to *k* − 1 by the whole output that the
    populations alone do not give (module docstring; D424)."""
    _, found = ran(name, viewed, worlded)
    assert [leak for leak in found.leaks if claimed(leak)] == []


def claimed(leak: tuple[Any, ...]) -> bool:
    """Whether a leak is of what the claim covers: at *k* = 2, a predicate alone keeps D320's
    statement and a cohort D297's, under which a map of reasons of two UNKNOWN units split one
    and one is pinned (§8.4), so at *k* = 2 maps are left out."""
    _, k, path, _, _ = leak
    breakdowns = ("unknown_by_reason", "unknown_by_leaf")
    is_map = template(path) in MAPS or path.split("/")[3] in breakdowns
    return k >= 3 or not is_map


@pytest.mark.parametrize("name", CASES)
def test_no_count_shown_is_from_one_to_k_less_one(
    viewed: Viewed, worlded: World, name: str
) -> None:
    """Nothing the output shows is a count from 1 to *k* − 1: the oracle above cannot see a
    gate that tests a count against too small a *k* where the count it shows pins nothing."""
    _, found = ran(name, viewed, worlded)
    assert found.small_shown == []


@pytest.mark.parametrize("name", CASES)
def test_what_the_output_nulls_is_what_the_scope_members_say(
    viewed: Viewed, worlded: World, name: str
) -> None:
    """In every world, a member is ``null`` exactly where ``disclosed_members`` gives it a pass
    from the same counts, and nothing else the output counts is ``null`` (the output is built
    from that one function)."""
    _, found = ran(name, viewed, worlded)
    assert found.unmatched == []


@pytest.mark.parametrize("name", CASES)
def test_the_members_never_shown_are_those_the_scope_s_shape_declares(
    viewed: Viewed, worlded: World, name: str
) -> None:
    """The members ``null`` in every world under *k* (a map of zeros beside a shown 0 aside,
    which that count gives) are exactly ``never_shown``'s for the scope's shape, so that what
    the secrets leave out is declared, not found."""
    model, found = ran(name, viewed, worlded)
    declared = never_shown(len(model.scope(0)), model.predicates)
    if model.predicates == 1:
        declared -= {VARIABLE_N, VARIABLE_UNITS, VARIABLE_MAP}
    assert found.nulled - found.shown == declared


@pytest.mark.parametrize("name", CASES)
def test_a_gated_scope_shows_no_map_and_no_joint_count_under_any_k(
    viewed: Viewed, worlded: World, name: str
) -> None:
    """Of a scope of two predicates or more, or of two positions or more, no map of reasons (of
    a proportion or of a ``variables`` entry) and no count of ``analysed`` is shown in any world
    under any *k*, and no ``lift_differs`` anywhere (D424): written out, so that a change to
    ``never_shown`` that shows one fails here whether or not a world pins it. And per
    predicate (``_flag_expected``): no caveat names a flag of the answers of a predicate with a
    ``null`` numerator or denominator, and one is named for a predicate whose two are shown."""
    _, found = ran(name, viewed, worlded)
    assert found.exposed == []


# --- What the gate shows ----------------------------------------------------------------------


def _shown(outcome: Outcome, position: int) -> list[tuple[int | None, int | None]]:
    return [
        (share.proportion.numerator, share.proportion.denominator)
        for share in outcome.values.positions[position].predicates
    ]


def test_a_view_that_refuses_overlap_withholds_one_position_and_shows_the_other(
    viewed: Viewed, worlded: World
) -> None:
    """Each position of a view that refuses overlap is a scope of its own: one whose split is
    suppressed is withheld whole, beside one shown whole (the gate is not the view's)."""
    view = viewed(2, 2, k=3)
    cohorts = ["T" * 10 + "F" * 12, "F" * 10 + "T" * 12]
    predicates = ["TTTTTFFFAA" + "TTTTFFFFAAAA", "TTTTTFFFFF" + "TTTTTTFFFFFF"]
    outcome = worlded(view, cohorts, predicates, k=3)
    assert _shown(outcome, 0) == [(None, None), (None, None)]
    assert _shown(outcome, 1) == [(4, 8), (6, 12)]
    assert outcome.analysed[1].variables is not None
    assert [v.excluded_units for v in outcome.analysed[1].variables] == [4, 0]


def test_a_reason_count_below_k_leaves_a_gated_scope_s_splits_shown(
    viewed: Viewed, worlded: World
) -> None:
    """A gated scope's maps are never shown, so a reason count from 1 to *k* − 1 withholds
    nothing: the splits are shown and the maps ``null`` (no map is in the gate)."""
    view = viewed(1, 2, k=3)
    predicates = ["TTTTTTFFFFFF" + "AIII", "TTTTTTTTFFFFFFFF"]
    outcome = worlded(view, ["T" * 16], predicates, k=3)
    assert _shown(outcome, 0) == [(6, 12), (8, 16)]
    shares = outcome.values.positions[0].predicates
    assert shares[0].proportion.excluded is None
    variables = outcome.analysed[0].variables
    assert variables is not None
    first = variables[0]
    assert (first.n, first.excluded_units, first.excluded) == (12, 4, None)


def test_a_cohort_s_small_unknown_units_withhold_nothing_where_overlap_is_refused(
    viewed: Viewed, worlded: World
) -> None:
    """Where overlap is refused only the position's ``n_true`` is in its gate: its cohort's
    suppressed UNKNOWN units count units outside it, which its predicates do not read."""
    view = viewed(1, 2, k=3)
    cohort = "T" * 12 + "F" * 5 + "AA"
    predicates = ["TTTTTTFFFFFF" + "F" * 7, "TTTTFFFFAAAA" + "F" * 7]
    outcome = worlded(view, [cohort], predicates, k=3)
    assert outcome.population[0].n_unknown is None
    assert outcome.population[0].n_true == 12
    assert _shown(outcome, 0) == [(6, 12), (4, 8)]


def test_one_predicate_alone_keeps_d320_s_rule_and_its_map(viewed: Viewed, worlded: World) -> None:
    """One predicate at a position of a view that refuses overlap is a predicate alone: its
    numerator is shown beside a suppressed split (D320), its map where none of its counts is
    small, and ``analysed`` is its split's."""
    view = viewed(2, 1, k=3)
    cohorts = ["T" * 10 + "F" * 12, "F" * 10 + "T" * 12]
    predicates = ["TTTTTTTTTA" + "TTTTFFFFAAAA"]
    outcome = worlded(view, cohorts, predicates, k=3)
    assert _shown(outcome, 0) == [(None, None)]
    [share] = outcome.values.positions[1].predicates
    assert (share.proportion.numerator, share.proportion.denominator) == (4, 8)
    assert share.proportion.excluded is not None
    assert share.proportion.excluded[ExclusionReason.NOT_ASSESSED] == 4
    assert (outcome.analysed[1].n, outcome.analysed[1].excluded_units) == (8, 4)
    view = viewed(1, 1, k=3)
    outcome = worlded(view, ["T" * 10], ["TTTTTTTTTF"], k=3)
    assert _shown(outcome, 0) == [(None, 10)]


def test_one_predicate_at_two_positions_that_may_share_units_is_gated(
    viewed: Viewed, worlded: World
) -> None:
    """One predicate at two positions of a view that allows overlap is a gated scope: shown
    whole where nothing in it is hidden, its maps and ``analysed`` never."""
    view = viewed(2, 1, k=3, overlap=True)
    cohorts = ["T" * 20, "T" * 10 + "F" * 10]
    predicates = ["TTTFFFAAAA" + "TTTTTFFFFF"]
    outcome = worlded(view, cohorts, predicates, k=3)
    assert _shown(outcome, 0) == [(8, 16)]
    assert _shown(outcome, 1) == [(3, 6)]
    for at in range(2):
        assert outcome.values.positions[at].predicates[0].proportion.excluded is None
        assert (outcome.analysed[at].n, outcome.analysed[at].excluded_units) == (None, None)
    outcome = worlded(view, ["T" * 20, "T" * 10 + "F" * 9 + "A"], predicates, k=3)
    assert _shown(outcome, 0) == _shown(outcome, 1) == [(None, None)]


def _answers(units: int, missing: int, reasons: str, salt: int) -> str:
    """A column's answers over ``units`` units: ``missing`` in each 100 UNKNOWN (for each of
    ``reasons`` in turn), and of the rest 2 in each 5 TRUE, spread by ``salt`` so that two
    columns are about independent."""
    found = []
    for unit in range(units):
        at = (unit * salt + salt // 2) % 100
        if at < missing:
            found.append(reasons[at % len(reasons)])
        else:
            found.append("T" if at % 5 < 2 else "F")
    return "".join(found)


COSTS = [
    (units, missing, k, reasons, positions)
    for units in (200, 350, 500)
    for missing in (5, 10)
    for k in (5, 10)
    for reasons in ("A", "AI")
    for positions in (1, 2)
    if units // positions * missing // 100 >= k
]
"""Ordinary views: two predicates over independent columns, 200 to 500 units, 5 % or 10 %
missing for one reason or two, under *k* of 5 or 10, over one position or two that refuse
overlap, where a position's UNKNOWN units are *k* or more (175 units 5 % missing have 8, which
*k* = 10 suppresses, and D320 hid too)."""


@pytest.mark.parametrize(("units", "missing", "k", "reasons", "positions"), COSTS)
def test_ordinary_views_of_two_predicates_show_every_predicate(
    viewed: Viewed,
    worlded: World,
    units: int,
    missing: int,
    k: int,
    reasons: str,
    positions: int,
) -> None:
    """The gate's cost: where no count of a split is small, both predicates are shown at every
    position, their maps and ``analysed``'s joint counts ``null``."""
    view = viewed(positions, 2, k=k)
    half = units // positions
    cohorts = [
        "F" * half * at + "T" * half + "F" * half * (positions - 1 - at) for at in range(positions)
    ]
    predicates = [_answers(units, missing, reasons, salt) for salt in (37, 61)]
    outcome = worlded(view, cohorts, predicates, k=k)
    for at in range(positions):
        assert all(n is not None and d is not None for n, d in _shown(outcome, at))
        assert all(s.proportion.excluded is None for s in outcome.values.positions[at].predicates)
        assert outcome.analysed[at].n is None


def _flags(outcome: Outcome) -> list[CaveatCode]:
    return [c.code for c in outcome.caveats if c.code in FLAGS and "/values" in c.affects]


def test_a_withheld_scope_names_no_flag_of_its_predicates(viewed: Viewed, worlded: World) -> None:
    """The flags a predicate's answers raise (``COVERAGE_PROPOSED`` here, a lower-case answer)
    are named for a scope shown, and not for one withheld: that one unit relied on proposed
    coverage is a count of 1 or more (D424)."""
    view = viewed(1, 2, k=3)
    other = "TTTTTTFFFFFF"
    withheld = worlded(view, ["T" * 12], ["tTTTTTFFFFAA", other], k=3)
    assert _shown(withheld, 0) == [(None, None), (None, None)]
    assert _flags(withheld) == []
    shown = worlded(view, ["T" * 12], ["tTTTTTFFFAAA", other], k=3)
    assert _shown(shown, 0) == [(6, 9), (6, 12)]
    assert _flags(shown) == [CaveatCode.COVERAGE_PROPOSED]


def test_a_predicate_alone_that_is_withheld_names_no_flag_of_its_answers(
    viewed: Viewed, worlded: World
) -> None:
    """The same of a predicate alone (D320's rule, D424's flags): where its split is suppressed,
    or its position's ``n_true`` is, its numerator and denominator are ``null`` and no caveat
    says that one of its answers relied on proposed coverage; shown, it does."""
    view = viewed(1, 1, k=3)
    split = worlded(view, ["T" * 12], ["tTTTTTTTTTFA"], k=3)
    assert _shown(split, 0) == [(None, None)]
    assert _flags(split) == []
    shown = worlded(view, ["T" * 12], ["tTTTTTFFFAAA"], k=3)
    assert _shown(shown, 0) == [(6, 9)]
    assert _flags(shown) == [CaveatCode.COVERAGE_PROPOSED]
    view = viewed(2, 1, k=3)
    cohorts = ["TT" + "F" * 28, "FF" + "T" * 14 + "F" * 14]
    small = worlded(view, cohorts, ["tF" + "T" * 7 + "F" * 7 + "F" * 14], k=3)
    assert small.population[0].n_true is None
    assert _shown(small, 0) == [(None, None)]
    assert _shown(small, 1) == [(7, 14)]
    assert _flags(small) == []
    view = viewed(1, 1, k=3)
    numerator = worlded(view, ["T" * 10], ["tTTTTTTTTF"], k=3)
    assert _shown(numerator, 0) == [(None, 10)]
    assert _flags(numerator) == []
    denominator = worlded(view, ["T" * 16], ["tTTTTTTTTFFFFFAT"], k=3)
    assert _shown(denominator, 0) == [(10, None)]
    assert _flags(denominator) == []


def test_a_scope_withheld_by_any_one_pass_is_withheld_whole(viewed: Viewed, worlded: World) -> None:
    """Each pass the gate holds withholds the scope by itself, the others passing: a split's
    small TRUE units, its small FALSE units, its small UNKNOWN units (a count from 1 to
    *k* − 1 of each), the small table's rule alone (a split of 3 units, none small, at *k* = 3)
    and a position's ``n_true``; the split in the second predicate, so that the first alone
    does not decide it."""
    view = viewed(1, 2, k=5)
    whole = "T" * 24
    first = "T" * 8 + "F" * 8 + "A" * 8
    for second in (
        "T" * 3 + "F" * 13 + "A" * 8,  # TRUE units from 1 to k - 1
        "T" * 8 + "F" * 3 + "A" * 13,  # FALSE units
        "T" * 8 + "F" * 15 + "A" * 1,  # UNKNOWN units
    ):
        withheld = worlded(view, [whole], [first, second], k=5)
        assert _shown(withheld, 0) == [(None, None), (None, None)], second
    shown = worlded(view, [whole], [first, "T" * 6 + "F" * 10 + "A" * 8], k=5)
    assert _shown(shown, 0) == [(8, 16), (6, 16)]
    view = viewed(1, 2, k=3)
    tiny = worlded(view, ["T" * 3 + "F" * 27], ["TTT" + "F" * 27, "TTT" + "F" * 27], k=3)
    assert tiny.population[0].n_true == 3
    assert _shown(tiny, 0) == [(None, None), (None, None)]  # the small table's rule alone
    no_size = worlded(view, ["T" * 2 + "F" * 28], ["TT" + "F" * 28, "TF" + "F" * 28], k=3)
    assert no_size.population[0].n_true is None
    assert _shown(no_size, 0) == [(None, None), (None, None)]


# --- Mutation notes ---------------------------------------------------------------------------
#
# Mutants of ``disclosure.py`` and ``existence.py`` that this file (with ``test_disclosure`` and
# the end-to-end tests) is held to, each killed by the tests named:
#
# - the allow gate without a population member (``whole_shown`` ignoring a hidden
#   ``unknown_by_reason``, ``unknown_by_leaf``, or deciding by ``n_unknown`` alone), and without
#   the population half (``allow`` dropped from ``scope_shown``): the leak oracle over
#   ``allow_reasons``, ``clauses_two`` and ``allow_probe`` (worlds of 6 to 11 units at *k* = 3,
#   where a population member is hidden beside splits that ``suppressed`` leaves shown), not a
#   copy of the code's expression: ``_whole_shown`` reads which population members the output
#   nulls. At *k* = 4 these models run at 6 units, which cannot hide a population member beside
#   shown splits (it takes about 10), so there they test only the gate's other half; the
#   population half is exercised at *k* = 3;
# - a flag named for a predicate with a ``null`` numerator or denominator (``withheld`` of
#   ``compare`` keeping only ``Pass.SCOPE``, or ``and``, or one of the two): the structural
#   check's per-predicate flag rule (``_flag_expected``) over ``flagged_alone``, and
#   ``test_a_predicate_alone_that_is_withheld_names_no_flag_of_its_answers`` (its one-sided
#   shapes); a withheld scope's flags: ``test_a_withheld_scope_names_no_flag_of_its_predicates``;
# - ``zero_map_le1`` (the zero-map exception of ``_scope_members`` for a predicate whose UNKNOWN
#   count is at most 1, not 0): **equivalent**. The exception reads a shown UNKNOWN count, which
#   under *k* of 2 or more is 0 or at least *k* (a 1 is small and is suppressed), and where
#   nothing is suppressed (no *k*) the map is shown anyway.

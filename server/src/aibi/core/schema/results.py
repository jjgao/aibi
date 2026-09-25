"""Result envelopes, cohort counts and catalogue statistic references (SPEC §8.1).

Every result carries its derivation. A number that cannot be computed is ``null`` with its
reason (§8.2). A string from data or a document is wrapped as ``{"data": …}`` or lies in a
schema node marked ``x-aibi-data`` (A6). The invariants that tie the parts together are checked
here, so an output that breaks them cannot be built:

- one to six cohorts, whose positions count from 0, and per-position arrays that line up;
- ``analysed.n`` plus ``excluded_units`` is the position's ``n_true``;
- every caveat's ``affects`` names a digested part of the output;
- caveats are carried when what raises them is in the digested parts (never in the documents
  or parameters, which hold user text): ``NOT_ESTIMABLE`` exactly when a number other than a
  suppressed one is not estimable, ``SUPPRESSED`` whenever something was suppressed (it may
  also be carried when categories were pooled or bins merged, which leave no ``null``),
  ``LIFT_DIFFERS`` exactly when a ``lift_differs`` count is above zero or suppressed,
  ``DRAFT_RELEASE`` exactly when a release is a draft, and ``UNKNOWN_EXCLUDED``,
  ``INVALID_EXCLUDED``, ``COHORTS_OVERLAP`` and ``CONFOUNDED_WITH_DATASET`` whenever the counts
  or reasons that raise them are there;
- the rules of the disclosure pass that need no data (§8.4): nothing is suppressed, and no
  ``SUPPRESSED`` carried, without a ``min_cell_count``; no shown count lies between 1 and
  *k* − 1, nor any count of a shown breakdown; no linked set shows one suppressed member, or a
  suppressed total, through a non-zero other; with ``n_true`` shown, ``n`` and
  ``excluded_units`` are shown or suppressed together; a cohort count's size shows no
  complement from 1 to *k* − 1, and accounts for its counts as the pass leaves them;
- ``analysed.n`` is at least each variable's ``n`` and at most their sum, and so each
  variable excludes at least as many units; ``lift_differs`` is at most the size, and a
  suppressed one needs a size of at least 1.

Cohort counts carry their effective ``min_cell_count`` so that the same rules can be checked.
Proportions inside ``values`` are checked for their ``not_estimable`` maps only, until their
shape is typed (M3).
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Any, Literal, NamedTuple, Self
from urllib.parse import quote, unquote

from packaging.version import InvalidVersion, Version
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from aibi.core.schema.caveats import Caveat, CaveatCode, sort_caveats
from aibi.core.schema.descriptors import SemVer
from aibi.core.schema.document import PackKey
from aibi.core.schema.errors import problem
from aibi.core.schema.ids import (
    COLUMN_REF_RE,
    COVERAGE_ID_RE,
    DATASET_DESCRIPTOR_ID,
    ENDPOINT_ID_RE,
    IDENT,
    JSON_POINTER_RE,
    MAX_SAFE_INTEGER,
    RELATIONSHIP_ID_RE,
    AnalysisId,
    DatasetId,
    DerivationId,
    IssuanceId,
    JsonPointer,
    LeafKey,
    Sha256,
)
from aibi.core.schema.jsonio import MISSING, is_text, lookup, utf16_key
from aibi.core.schema.limits import MAX_COHORTS, MAX_COLUMNS, MAX_POINTER
from aibi.core.schema.numbers import (
    ComputedCount,
    Estimable,
    ExclusionCounts,
    NotEstimableReason,
    Position,
    Proportion,
    RelativePointer,
    computed_nulls,
    estimable_schema,
    not_estimable_problems,
)
from aibi.core.schema.output import (
    COMPUTED,
    DATA_MARK,
    LAX,
    Count,
    Data,
    FiniteJsonObject,
    Output,
    Segment,
)
from aibi.core.schema.semantics import ExclusionReason, Reason

# --- Derivation, issuance and source ---------------------------------------------------------


def _pep440(value: str) -> str:
    try:
        canonical = str(Version(value))
    except InvalidVersion:
        raise problem("version", "Not a PEP 440 version") from None
    if canonical != value:
        raise problem("version", "Write the version in its normal form: ", shown=canonical)
    return value


_PEP440 = (
    r"^(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?"
    r"(?:\+[a-z0-9]+(?:\.[a-z0-9]+)*)?$"
)
Pep440 = Annotated[str, Field(max_length=64, pattern=_PEP440), AfterValidator(_pep440)]
"""An exact version in PEP 440's normalised form, such as ``1.2.0`` or ``1.0rc1``."""


SafeInt = Annotated[StrictInt, Field(ge=1, le=MAX_SAFE_INTEGER)]
"""A positive integer JSON text carries unchanged."""


class ReleaseRef(Output):
    """A release an output drew on. The label is recorded beside the manifest, never hashed."""

    model_config = ConfigDict(
        json_schema_extra={
            "if": {"properties": {"status": {"const": "draft"}}},
            "then": {"properties": {"label": {"const": "draft"}}},
            "else": {"properties": {"label": {"type": "integer"}}},
        }
    )

    dataset: DatasetId
    label: SafeInt | Literal["draft"]
    manifest: Sha256
    status: Literal["published", "draft"]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.label == "draft") != (self.status == "draft"):
            raise problem("release_status", 'A draft release has the label "draft"')
        return self


class AnalysisRef(Output):
    id: AnalysisId
    version: SemVer


class PackVersion(Output):
    version: Pep440
    results_version: SafeInt


class Disclosure(Output):
    """The effective disclosure setting of an output (SPEC §8.4)."""

    min_cell_count: Annotated[StrictInt, Field(ge=2, le=MAX_SAFE_INTEGER)] | None


def _releases(releases: list[ReleaseRef]) -> list[ReleaseRef]:
    """Releases by dataset id: each dataset resolves to one release (SPEC §7.1, §9.3).

    They are refused out of order rather than sorted, since caveats point at them by index."""
    datasets = [release.dataset for release in releases]
    if len(set(datasets)) != len(datasets):
        raise problem("duplicate_release", "Each dataset is listed once, with its one release")
    if datasets != sorted(datasets):
        raise problem("release_order", "Releases are listed by dataset id")
    return releases


Releases = Annotated[list[ReleaseRef], Field(min_length=1), AfterValidator(_releases)]


class Derivation(Output):
    """What an output was computed from, all of it hashed into its id (SPEC §7.6)."""

    id: DerivationId
    document: Annotated[FiniteJsonObject, Field(json_schema_extra=DATA_MARK)]
    """The canonical form, carried verbatim."""
    analysis: AnalysisRef
    releases: Releases
    packs: dict[PackKey, PackVersion]
    semantics_version: SafeInt
    disclosure: Disclosure
    engine: Annotated[str, Field(pattern=r"^aibi [0-9A-Za-z.+!_-]{1,64}$")]


class Issuance(Output):
    """One time an output was produced. ``values_from`` names the issuance whose SQL produced
    the values: this one, or for a cache hit the one that filled the cache."""

    id: IssuanceId
    cache_hit: StrictBool
    values_from: IssuanceId

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.cache_hit == (self.values_from == self.id):
            raise problem(
                "issuance",
                "values_from is the issuance's own id exactly when it is not a cache hit",
            )
        return self


class Source(Output):
    """The document as written, the leaves it maps to and the parameters used; outside every
    digest, and data as a whole (A6)."""

    model_config = ConfigDict(json_schema_extra=DATA_MARK)

    document: FiniteJsonObject
    leaves: dict[
        Annotated[JsonPointer, Field(min_length=1)],
        Annotated[list[LeafKey], Field(min_length=1)],
    ]
    """Each leaf of the document as written, by pointer (never the whole document), and the keys
    of the top-level clauses it became part of (SPEC §6.6)."""
    params: FiniteJsonObject

    @model_validator(mode="after")
    def _check(self) -> Self:
        if any(lookup(self.document, at) is MISSING for at in self.leaves):
            raise problem("source_leaves", "Every leaf pointer names a part of the document")
        return self


# --- Counts ----------------------------------------------------------------------------------


def _sorted_pointers(pointers: list[str]) -> list[str]:
    return sorted(set(pointers), key=utf16_key)


_EVERY_REASON: dict[str, JsonValue] = {"required": [reason.value for reason in Reason]}


_POPULATION_MEMBERS = (
    "n_true",
    "n_false",
    "n_unknown",
    "unknown_by_reason",
    "unknown_by_leaf",
    "lift_differs",
)


def _population_schema(schema: dict[str, Any]) -> None:
    """JSON Schema: a member is ``null`` exactly when ``suppressed`` lists it, and the
    breakdowns are ``null`` with ``n_unknown``."""
    rules: list[dict[str, Any]] = []
    for member in _POPULATION_MEMBERS:
        null = {"required": [member], "properties": {member: {"type": "null"}}}
        listed = {
            "required": ["suppressed"],
            "properties": {"suppressed": {"contains": {"const": "/" + member}}},
        }
        rules += [{"if": null, "then": listed}, {"if": listed, "then": null}]
    rules.append(
        {
            "if": {"required": ["n_unknown"], "properties": {"n_unknown": {"type": "null"}}},
            "then": {
                "properties": {
                    "unknown_by_reason": {"type": "null"},
                    "unknown_by_leaf": {"type": "null"},
                }
            },
        }
    )
    rules.append(
        {"properties": {"suppressed": {"items": {"enum": ["/" + m for m in _POPULATION_MEMBERS]}}}}
    )
    schema["allOf"] = rules


class Population(Output):
    """A cohort's accounting over the unit table (SPEC §6.6).

    A count is ``null`` only when the disclosure settings suppressed it, and ``suppressed``
    lists exactly those members. A breakdown is ``null`` with its total.
    """

    model_config = ConfigDict(json_schema_extra=_population_schema)

    n_true: ComputedCount
    n_false: ComputedCount
    n_unknown: ComputedCount
    unknown_by_reason: Annotated[
        Annotated[
            dict[Annotated[Reason, LAX], Count],
            Field(json_schema_extra=_EVERY_REASON),
        ]
        | None,
        COMPUTED,
    ]
    unknown_by_leaf: Annotated[dict[LeafKey, Count] | None, COMPUTED]
    lift_differs: ComputedCount
    suppressed: list[RelativePointer] = Field(default_factory=list[str])

    @field_validator("suppressed")
    @classmethod
    def _sort(cls, pointers: list[str]) -> list[str]:
        return _sorted_pointers(pointers)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if set(self.suppressed) != set(computed_nulls(self)):
            raise problem("population_suppressed", "suppressed lists exactly the null counts")
        if self.n_unknown is None and (
            self.unknown_by_reason is not None or self.unknown_by_leaf is not None
        ):
            raise problem("population_breakdown", "A breakdown is suppressed with its total")
        by_reason = self.unknown_by_reason
        if by_reason is not None and set(by_reason) != set(Reason):
            raise problem("population_reasons", "unknown_by_reason lists every reason")
        unknown = self.n_unknown
        by_leaf = self.unknown_by_leaf
        if unknown == 0 and (by_reason is None or by_leaf is None):
            raise problem(
                "population_breakdown", "The breakdowns of a zero n_unknown are zeros, not null"
            )
        if unknown is not None:
            for breakdown in (by_reason, by_leaf):
                if breakdown is not None and any(count > unknown for count in breakdown.values()):
                    raise problem("population_breakdown", "No breakdown count exceeds n_unknown")
            if by_reason is not None and unknown > 0 and sum(by_reason.values()) < unknown:
                raise problem("population_reasons", "Every unknown unit has at least one reason")
            # An unknown unit is unknown for at least one top-level clause (SPEC §6.3).
            if by_leaf is not None and unknown > 0 and sum(by_leaf.values()) < unknown:
                raise problem(
                    "population_leaves", "Every unknown unit is unknown for some top-level clause"
                )
        counts = (self.n_true, self.n_false, self.n_unknown, self.lift_differs)
        if None not in counts:
            n_true, n_false, n_unknown, lift_differs = (int(count or 0) for count in counts)
            if lift_differs > n_true + n_false + n_unknown:
                raise problem("population_lift", "lift_differs counts units of the unit table")
        return self

    def lift_differs_or_suppressed(self) -> bool:
        return self.lift_differs is None or self.lift_differs > 0


def _suppressed_only(schema: dict[str, Any]) -> None:
    """JSON Schema: these counts are null only when suppressed."""
    marks = schema["properties"]["not_estimable"]
    for branch in marks.get("anyOf", [marks]):
        if branch.get("type") == "object":
            branch["patternProperties"] = {
                pattern: {"const": NotEstimableReason.SUPPRESSED.value}
                for pattern in branch["patternProperties"]
            }


def _analysed_schema(schema: dict[str, Any], model: type[BaseModel]) -> None:
    _suppressed_only(schema)
    estimable_schema(schema, model)


class AnalysedCounts(Estimable):
    """Units analysed and excluded, by reason (SPEC §8.1). A unit counts under each of its
    reasons in ``excluded``, which lists every reason, and once in ``excluded_units``; ``null``
    only when suppressed."""

    model_config = ConfigDict(json_schema_extra=_analysed_schema)

    n: ComputedCount
    excluded: Annotated[ExclusionCounts | None, COMPUTED]
    excluded_units: ComputedCount

    @model_validator(mode="after")
    def _check_counts(self) -> Self:
        if any(reason != NotEstimableReason.SUPPRESSED for reason in self.reasons().values()):
            raise problem("analysed_suppressed", "Counts are null only when suppressed")
        if self.excluded_units is None and self.excluded is not None:
            raise problem("analysed_breakdown", "excluded is suppressed with excluded_units")
        if self.excluded_units == 0 and self.excluded is None:
            raise problem(
                "analysed_breakdown", "The breakdown of a zero excluded_units is zeros, not null"
            )
        if self.excluded is not None and set(self.excluded) != set(ExclusionReason):
            raise problem("analysed_excluded", "excluded lists every exclusion reason")
        if self.excluded is not None and self.excluded_units is not None:
            counts = self.excluded.values()
            if not max(counts, default=0) <= self.excluded_units <= sum(counts):
                raise problem(
                    "analysed_excluded",
                    "excluded_units is at least the largest count in excluded and at most "
                    "their sum",
                )
        return self

    def total(self) -> int | None:
        """``n`` plus ``excluded_units``: the position's ``n_true``, when both are shown."""
        if self.n is None or self.excluded_units is None:
            return None
        return self.n + self.excluded_units


class AnalysedVariable(AnalysedCounts):
    pass


class Analysed(AnalysedCounts):
    variables: Annotated[list[AnalysedVariable], Field(min_length=2)] | None = None
    """One entry per column or predicate, in parameter order, for views over several."""

    @model_validator(mode="after")
    def _check_variables(self) -> Self:
        given = [variable.n for variable in self.variables or []]
        shown = [n for n in given if n is not None]
        if self.n is not None and any(n > self.n for n in shown):
            raise problem(
                "analysed_variables",
                "n counts the units analysed for at least one variable, so no variable's n is "
                "larger",
            )
        if self.n is not None and given and len(shown) == len(given) and self.n > sum(shown):
            raise problem(
                "analysed_variables",
                "n counts the units analysed for at least one variable, so it is at most the "
                "sum of theirs",
            )
        excluded = self.excluded_units
        if excluded is not None and any(
            variable.excluded_units is not None and variable.excluded_units < excluded
            for variable in self.variables or []
        ):
            raise problem(
                "analysed_variables",
                "No variable's n is larger than n, so no variable excludes fewer units",
            )
        return self


class Values(Output):
    """Analysis values: per position in view order, and for the view as a whole.

    Their shape is the analysis entry's ``returns`` (M3); until then the whole of it is data
    (A6), and only its ``not_estimable`` maps are checked.
    """

    model_config = ConfigDict(json_schema_extra=DATA_MARK)

    positions: list[FiniteJsonObject]
    view: FiniteJsonObject

    @model_validator(mode="after")
    def _check(self) -> Self:
        problems = not_estimable_problems({"positions": list(self.positions), "view": self.view})
        if problems:
            # The message is fixed; the pointer, whose keys come from data, is shown as data.
            raise problem(
                "values_not_estimable",
                "A not_estimable map in values names no null member, or holds no reason: ",
                shown=problems[0],
            )
        return self


class Readback(Output):
    cohorts: list[list[Segment]]
    view: list[Segment]


class CohortRef(Output):
    position: Position
    id: DerivationId
    reference: StrictBool


Chart = FiniteJsonObject
"""A Vega-Lite specification with inline data (SPEC §8.5)."""


# --- The result envelope ---------------------------------------------------------------------


def _not_estimable_reasons(value: JsonValue) -> list[str]:
    """Every reason in every ``not_estimable`` map inside a JSON value."""
    reasons: list[str] = []
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, dict):
            marks = current.get("not_estimable")
            if isinstance(marks, dict):
                reasons.extend(str(reason) for reason in marks.values())
            pending.extend(member for key, member in current.items() if key != "not_estimable")
    return reasons


_AFFECTED = ("/cohorts", "/population", "/analysed", "/values")
"""What a caveat may affect: the digested parts of a result, and ``/derivation/releases`` for
``DRAFT_RELEASE`` (SPEC §8.1, §8.3)."""


def _under_pattern(prefixes: Sequence[str]) -> str:
    """A pattern for the pointers at or under one of ``prefixes``."""
    return "^(?:" + "|".join(re.escape(prefix) for prefix in prefixes) + r")(?:/[\s\S]*)?$"


def _envelope_schema(schema: dict[str, Any]) -> None:
    """JSON Schema: cohort positions count from 0, caveats affect the digested parts (and, for
    DRAFT_RELEASE, the releases), and an output over a draft is never a cache hit."""
    cohorts = schema["properties"]["cohorts"]
    cohorts["prefixItems"] = [
        {"properties": {"position": {"const": position}}} for position in range(MAX_COHORTS)
    ]
    draft = CaveatCode.DRAFT_RELEASE.value
    schema["properties"]["caveats"]["items"] = {
        "allOf": [
            schema["properties"]["caveats"]["items"],
            {
                "if": {"properties": {"code": {"const": draft}}},
                "then": {
                    "properties": {
                        "affects": {
                            "items": {
                                "pattern": _under_pattern([*_AFFECTED, "/derivation/releases"])
                            }
                        }
                    }
                },
                "else": {
                    "properties": {"affects": {"items": {"pattern": _under_pattern(_AFFECTED)}}}
                },
            },
        ]
    }
    schema["allOf"] = [
        {
            "if": {
                "properties": {
                    "derivation": {
                        "properties": {
                            "releases": {"contains": {"properties": {"status": {"const": "draft"}}}}
                        }
                    }
                }
            },
            "then": {"properties": {"issuance": {"properties": {"cache_hit": {"const": False}}}}},
        }
    ]


_UNKNOWN_REASONS = frozenset(reason.value for reason in Reason)


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def _check_caveats(
    caveats: list[Caveat],
    output: JsonValue,
    required: dict[CaveatCode, bool],
    *,
    exactly: frozenset[CaveatCode],
) -> None:
    """Every caveat affects a part of ``output``; the codes whose condition holds are carried,
    and those in ``exactly`` only then."""
    for caveat in caveats:
        missing = [path for path in caveat.affects if lookup(output, path) is MISSING]
        if missing:
            raise problem(
                "caveat_affects",
                "A caveat affects a path the output lacks: ",
                shown=missing[0],
            )
    codes = {caveat.code for caveat in caveats}
    for code, needed in required.items():
        if needed and code not in codes:
            raise problem("caveat_missing", "The output must carry {code}", code=code.value)
        if not needed and code in codes and code in exactly:
            raise problem(
                "caveat_unfounded", "Nothing in the output raises {code}", code=code.value
            )


def _excluded_by(
    counts: Iterable[dict[ExclusionReason, int] | None], reasons: frozenset[str]
) -> bool:
    return any(
        count > 0
        for excluded in counts
        if excluded is not None
        for reason, count in excluded.items()
        if reason in reasons
    )


def _small(k: int | None, count: int | None) -> bool:
    """Whether a shown count lies from 1 to *k* − 1, which the disclosure pass suppresses."""
    return k is not None and count is not None and 0 < count < k


def _revealing(members: Sequence[int | None]) -> bool:
    """Whether a linked set whose total is known shows its one suppressed member through the
    others. The pass then suppresses a second one, unless every other member is 0 (SPEC §8.4)."""
    nulls = sum(member is None for member in members)
    return nulls == 1 and any(member for member in members if member is not None)


def _check_accounting(population: Population, denominator: int, k: int | None) -> None:
    """The unit table's size accounts for the population, suppressed counts included, as the
    disclosure pass leaves them (§8.4). A suppressed count counts at least one unit. One
    suppressed beside two zeros is the whole table, from 1 to *k* − 1. Two suppressed beside a
    shown count are both from 1 to *k* − 1, or one is and the other is the smallest non-zero
    other count, so no larger than the shown one (and smaller when the shown one comes first in
    the listed order, which wins a tie). All three suppressed are each from 1 to *k* − 1 or the
    first is the complement of two small ones, which with *k* = 2 means one unit each."""
    parts = (population.n_true, population.n_false, population.n_unknown)
    shown = [part for part in parts if part is not None]
    hidden = len(parts) - len(shown)
    if not hidden and denominator != sum(shown):
        raise problem("count_size", "size.denominator is n_true + n_false + n_unknown")
    if sum(shown) + hidden > denominator:
        raise problem(
            "count_size",
            "size.denominator is n_true + n_false + n_unknown, and a suppressed count is at "
            "least 1",
        )
    if hidden == 1 and not any(shown) and k is not None and not 0 < denominator < k:
        raise problem(
            "count_size",
            "A count suppressed beside two zeros is the unit table's size, from 1 to "
            "min_cell_count - 1",
        )
    if hidden == 2 and k is not None and shown[0] > 0:
        at = next(index for index, part in enumerate(parts) if part is not None)
        # The second one suppressed is at most the shown one; below it if that one is first.
        second = shown[0] if at > 0 else shown[0] - 1
        if denominator - shown[0] > max(2 * (k - 1), k - 1 + second):
            raise problem(
                "count_size",
                "Two suppressed counts are small, or one is and the other is the smallest "
                "non-zero other count: they add up to more than the pass leaves (§8.4)",
            )
    if hidden == 3 and k == 2 and denominator != 3:
        raise problem(
            "count_size",
            "With min_cell_count 2, all three counts are suppressed only when each is 1",
        )
    lift_differs = population.lift_differs
    if lift_differs is not None and lift_differs > denominator:
        raise problem("population_lift", "lift_differs counts units of the unit table")
    if lift_differs is None and denominator < 1:
        raise problem("population_lift", "A suppressed lift_differs is at least 1")


def _check_disclosure(
    k: int | None,
    population: Population,
    analysed: Sequence[AnalysedCounts],
    suppressed: bool,
) -> None:
    """The rules of the disclosure pass that need no data, for one position (SPEC §8.4).

    ``analysed`` holds the position's analysed counts and those of each variable; ``suppressed``
    is whether anything in the output was suppressed or ``SUPPRESSED`` is carried."""
    if suppressed and k is None:
        raise problem(
            "disclosure",
            "Nothing is suppressed, and SUPPRESSED is not carried, without a min_cell_count (§8.4)",
        )
    counts = [population.n_true, population.n_false, population.n_unknown]
    counts.append(population.lift_differs)
    breakdowns: list[Mapping[Any, int] | None] = [
        population.unknown_by_reason,
        population.unknown_by_leaf,
    ]
    for entry in analysed:
        counts += [entry.n, entry.excluded_units]
        breakdowns.append(entry.excluded)
    if any(_small(k, count) for count in counts):
        raise problem("disclosure", "A shown count lies between 1 and min_cell_count - 1 (§8.4)")
    if any(
        breakdown is not None and any(_small(k, count) for count in breakdown.values())
        for breakdown in breakdowns
    ):
        raise problem(
            "disclosure",
            "A breakdown with a count between 1 and min_cell_count - 1 is null as a whole (§8.4)",
        )
    linked = (population.n_true, population.n_false, population.n_unknown)
    if _revealing(linked):
        raise problem(
            "disclosure",
            "n_true, n_false and n_unknown show a suppressed one through the others and the "
            "unit table's size; a second one is suppressed too (§8.4)",
        )
    for entry in analysed:
        if population.n_true is not None and (entry.n is None) != (entry.excluded_units is None):
            raise problem(
                "disclosure",
                "With n_true shown, n and excluded_units are shown or suppressed together: one "
                "alone would show the other (§8.4)",
            )
        if population.n_true is None and entry.total() is not None:
            raise problem(
                "disclosure",
                "n and excluded_units would show a suppressed n_true; one is suppressed too",
            )
        if _revealing((entry.n, entry.excluded_units, population.n_true)):
            raise problem(
                "disclosure",
                "n, excluded_units and n_true show a suppressed one through the others; a "
                "second one is suppressed too (§8.4)",
            )


class ResultEnvelope(Output):
    """One view's result (SPEC §8.1)."""

    model_config = ConfigDict(json_schema_extra=_envelope_schema)

    derivation: Derivation
    issuance: Issuance
    source: Source
    digest: Sha256
    cohorts: Annotated[
        list[CohortRef],
        Field(
            min_length=1,
            max_length=MAX_COHORTS,
            json_schema_extra={
                "contains": {"properties": {"reference": {"const": True}}},
                "minContains": 0,
                "maxContains": 1,
            },
        ),
    ]
    population: list[Population]
    analysed: list[Analysed]
    values: Values
    caveats: list[Caveat]
    readback: Readback
    labels: list[Data]
    charts: Annotated[list[Chart], Field(json_schema_extra=DATA_MARK)]

    @field_validator("caveats")
    @classmethod
    def _sort_caveats(cls, caveats: list[Caveat]) -> list[Caveat]:
        return sort_caveats(caveats)

    @model_validator(mode="after")
    def _check(self) -> Self:
        positions = [cohort.position for cohort in self.cohorts]
        if positions != list(range(len(positions))):
            raise problem("result_positions", "Cohort positions count from 0 in view order")
        if sum(cohort.reference for cohort in self.cohorts) > 1:
            raise problem("result_reference", "At most one cohort is the reference")
        lengths = {
            len(self.population),
            len(self.analysed),
            len(self.values.positions),
            len(self.readback.cohorts),
            len(self.labels),
        }
        if lengths != {len(positions)}:
            raise problem(
                "result_positions",
                "population, analysed, values.positions, readback.cohorts and labels have one "
                "entry per cohort",
            )
        for population, analysed in zip(self.population, self.analysed, strict=True):
            for counts in [analysed, *(analysed.variables or [])]:
                total = counts.total()
                n_true = population.n_true
                if total is not None and n_true is not None and total != n_true:
                    raise problem(
                        "result_analysed", "n plus excluded_units is the position's n_true"
                    )
        for caveat in self.caveats:
            for path in caveat.affects:
                if not _under(path, _AFFECTED) and not (
                    caveat.code == CaveatCode.DRAFT_RELEASE
                    and _under(path, ("/derivation/releases",))
                ):
                    raise problem(
                        "caveat_affects",
                        "Caveats affect cohorts, population, analysed or values; DRAFT_RELEASE "
                        "may also affect derivation releases",
                    )
        draft = any(release.status == "draft" for release in self.derivation.releases)
        if draft and self.issuance.cache_hit:
            raise problem("draft_cached", "Outputs over a draft release are never cached (§12.3)")
        k = self.derivation.disclosure.min_cell_count
        suppressed_counts = any(population.suppressed for population in self.population)
        # What raises a caveat is found in the digested parts only, and caveats point only there
        # (and at the releases): documents and parameters hold user text (A6).
        digested = self.model_dump(
            mode="json",
            include={
                "cohorts": True,
                "population": True,
                "analysed": True,
                "values": True,
                "derivation": {"releases": True},
            },
        )
        reasons = _not_estimable_reasons([digested["analysed"], digested["values"]])
        codes = {caveat.code for caveat in self.caveats}
        suppressed = suppressed_counts or NotEstimableReason.SUPPRESSED.value in reasons
        for population, analysed in zip(self.population, self.analysed, strict=True):
            _check_disclosure(
                k,
                population,
                [analysed, *(analysed.variables or [])],
                suppressed or CaveatCode.SUPPRESSED in codes,
            )
        analysed_excluded = [
            counts.excluded
            for analysed in self.analysed
            for counts in [analysed, *(analysed.variables or [])]
        ]
        _check_caveats(
            self.caveats,
            digested,
            {
                CaveatCode.SUPPRESSED: suppressed,
                CaveatCode.NOT_ESTIMABLE: any(
                    reason != NotEstimableReason.SUPPRESSED.value for reason in reasons
                ),
                CaveatCode.LIFT_DIFFERS: any(
                    p.lift_differs_or_suppressed() for p in self.population
                ),
                CaveatCode.DRAFT_RELEASE: draft,
                # A suppressed count is never 0: a count of 0 is shown (SPEC §8.4).
                CaveatCode.UNKNOWN_EXCLUDED: any(
                    population.n_unknown != 0 for population in self.population
                )
                or _excluded_by(analysed_excluded, _UNKNOWN_REASONS),
                CaveatCode.INVALID_EXCLUDED: _excluded_by(
                    analysed_excluded, frozenset({ExclusionReason.INVALID_VALUE.value})
                ),
                CaveatCode.COHORTS_OVERLAP: NotEstimableReason.OVERLAPPING_COHORTS.value in reasons,
                CaveatCode.CONFOUNDED_WITH_DATASET: (
                    NotEstimableReason.CONFOUNDED_WITH_DATASET.value in reasons
                ),
            },
            exactly=frozenset(
                {CaveatCode.NOT_ESTIMABLE, CaveatCode.LIFT_DIFFERS, CaveatCode.DRAFT_RELEASE}
            ),
        )
        return self


_UNIT_TABLE: dict[str, JsonValue] = {"position": None, "predicate": None, "counts": "unit_table"}
_NOT_FOR_ONE_COHORT = frozenset(
    {
        CaveatCode.COHORTS_OVERLAP,
        CaveatCode.CONFOUNDED_WITH_DATASET,
        CaveatCode.INVALID_EXCLUDED,
    }
)
"""Caveats of analyses and views that a cohort count cannot carry (SPEC §8.3)."""
_NOT_FOR_ONE_COHORT_CODES: list[JsonValue] = [code.value for code in sorted(_NOT_FOR_ONE_COHORT)]


def _count_schema(schema: dict[str, Any]) -> None:
    """JSON Schema: a count over a draft release is never a cache hit (§12.3)."""
    draft: dict[str, JsonValue] = {"contains": {"properties": {"status": {"const": "draft"}}}}
    schema["allOf"] = [
        {
            "if": {"properties": {"releases": draft}},
            "then": {"properties": {"issuance": {"properties": {"cache_hit": {"const": False}}}}},
        }
    ]


class CohortCount(Output):
    """A cohort's count, as ``count_cohort`` returns it (SPEC §8.1)."""

    model_config = ConfigDict(json_schema_extra=_count_schema)

    id: DerivationId
    digest: Sha256
    population: Population
    size: Annotated[
        Proportion,
        Field(
            json_schema_extra={
                "allOf": [
                    {
                        "properties": {
                            "denominator_definition": {"const": _UNIT_TABLE},
                            "denominator": {"type": "integer"},
                        },
                        "not": {"required": ["excluded"]},
                    }
                ]
            }
        ),
    ]
    """The cohort's size over the unit table, from which nothing is excluded."""
    disclosure: Disclosure
    """The effective disclosure setting, over the cohort's datasets and the floor (SPEC §8.4)."""
    readback: list[Segment]
    caveats: Annotated[
        list[Caveat],
        Field(
            json_schema_extra={
                "allOf": [
                    {
                        "items": {
                            "properties": {"affects": {"const": ["/population"]}},
                            "not": {"properties": {"code": {"enum": _NOT_FOR_ONE_COHORT_CODES}}},
                        }
                    }
                ]
            }
        ),
    ]
    releases: Releases
    issuance: Issuance

    @field_validator("caveats")
    @classmethod
    def _sort_caveats(cls, caveats: list[Caveat]) -> list[Caveat]:
        return sort_caveats(caveats)

    @model_validator(mode="after")
    def _check(self) -> Self:
        definition = self.size.denominator_definition
        if (definition.position, definition.predicate, definition.counts) != (
            None,
            None,
            "unit_table",
        ):
            raise problem(
                "count_size",
                "size counts the unit table: position and predicate null, counts unit_table",
            )
        population = self.population
        if self.size.numerator != population.n_true:
            raise problem("count_size", "size.numerator is n_true, and suppressed with it")
        denominator = self.size.denominator
        if denominator is None:
            raise problem("count_size", "size.denominator, the unit table's size, is shown (§8.4)")
        _check_accounting(population, denominator, self.disclosure.min_cell_count)
        if "excluded" in self.size.model_fields_set:
            raise problem("count_size", "Nothing is excluded from the unit table: no excluded")
        if any(caveat.affects != ["/population"] for caveat in self.caveats):
            raise problem("count_affects", 'Caveats of a cohort count affect ["/population"]')
        codes = {caveat.code for caveat in self.caveats}
        if codes & _NOT_FOR_ONE_COHORT:
            raise problem(
                "count_caveat",
                "A cohort count carries no caveat about analyses or several cohorts: {codes}",
                codes=", ".join(sorted(codes & _NOT_FOR_ONE_COHORT)),
            )
        draft = any(release.status == "draft" for release in self.releases)
        if draft and self.issuance.cache_hit:
            raise problem("draft_cached", "Outputs over a draft release are never cached (§12.3)")
        reasons = list(self.size.reasons().values())
        suppressed = bool(population.suppressed) or NotEstimableReason.SUPPRESSED in reasons
        k = self.disclosure.min_cell_count
        _check_disclosure(k, population, [], suppressed or CaveatCode.SUPPRESSED in codes)
        # A proportion's numerator and its complement are a linked set; the size's denominator
        # is always shown, so a small complement is hidden by suppressing the numerator,
        # unless the numerator is 0 (SPEC §8.4).
        numerator = self.size.numerator
        if numerator and _small(k, denominator - numerator):
            raise problem(
                "disclosure",
                "size shows its complement, the units outside the cohort, between 1 and "
                "min_cell_count - 1; its numerator is suppressed too (§8.4)",
            )
        _check_caveats(
            self.caveats,
            self.model_dump(mode="json", include={"population"}),
            {
                CaveatCode.SUPPRESSED: suppressed,
                CaveatCode.NOT_ESTIMABLE: any(
                    reason != NotEstimableReason.SUPPRESSED for reason in reasons
                ),
                CaveatCode.LIFT_DIFFERS: population.lift_differs_or_suppressed(),
                CaveatCode.DRAFT_RELEASE: draft,
                CaveatCode.UNKNOWN_EXCLUDED: population.n_unknown != 0,
            },
            exactly=frozenset(
                {CaveatCode.NOT_ESTIMABLE, CaveatCode.LIFT_DIFFERS, CaveatCode.DRAFT_RELEASE}
            ),
        )
        return self


# --- Catalogue statistic references ----------------------------------------------------------

_RELEASE_DESCRIPTOR_ID = "|".join(
    [
        DATASET_DESCRIPTOR_ID,
        IDENT,
        COLUMN_REF_RE.pattern[1:-1],
        RELATIONSHIP_ID_RE.pattern[1:-1],
        COVERAGE_ID_RE.pattern[1:-1],
        ENDPOINT_ID_RE.pattern[1:-1],
    ]
)
STAT_REFERENCE_RE = re.compile(
    rf"^stat:(sha256:[0-9a-f]{{64}})/({_RELEASE_DESCRIPTOR_ID})((?:/[^/?#]*)*)"
    r"(?:\?floor=([1-9][0-9]{0,15}))?$"
)
_POINTER_SAFE = "/!$&'()*+,;=:@"
"""Characters a pointer keeps in a reference besides the unreserved ones; the rest, ``?``, ``#``
and ``%`` included, are percent-encoded from UTF-8 with upper-case hex (RFC 6901 §6)."""


class StatReference(NamedTuple):
    manifest: str
    descriptor_id: str
    pointer: str
    floor: int | None


def stat_reference(
    manifest: str, descriptor_id: str, pointer: str, floor: int | None = None
) -> str:
    """``stat:<manifest>/<descriptor id><pointer>``, with ``?floor=<n>`` when a deployment floor
    applies (SPEC §8.1, §8.4). The pointer is written in its URI fragment form, so a reference
    has exactly one spelling. Raises ``ValueError`` for parts that are not what they claim."""
    if JSON_POINTER_RE.fullmatch(pointer) is None or len(pointer) > MAX_POINTER:
        raise ValueError("not a JSON Pointer of at most 16,384 characters")
    if not is_text(pointer):
        raise ValueError("a JSON Pointer is Unicode text: no lone surrogates or noncharacters")
    if "__" in descriptor_id:
        raise ValueError("descriptor ids hold no __")
    if descriptor_id.startswith(("rel:", "cov:")) and descriptor_id.count("+") >= MAX_COLUMNS:
        raise ValueError("a relationship has at most 16 columns")
    if floor is not None and not 2 <= floor <= MAX_SAFE_INTEGER:
        raise ValueError("a floor is from 2 to 2^53 - 1")
    reference = f"stat:{manifest}/{descriptor_id}{quote(pointer, safe=_POINTER_SAFE)}"
    if floor is not None:
        reference += f"?floor={floor}"
    if STAT_REFERENCE_RE.fullmatch(reference) is None:
        raise ValueError("not a statistic reference")
    return reference


def parse_stat_reference(reference: str) -> StatReference:
    """The parts of a reference ``stat_reference`` wrote; any other spelling is refused."""
    match = STAT_REFERENCE_RE.fullmatch(reference)
    if match is None:
        raise ValueError("not a statistic reference")
    manifest, descriptor_id, encoded, floor = match.groups()
    try:
        pointer = unquote(encoded, errors="strict")
    except UnicodeDecodeError:
        raise ValueError("the pointer is not percent-encoded UTF-8") from None
    parsed = StatReference(manifest, descriptor_id, pointer, int(floor) if floor else None)
    if stat_reference(*parsed) != reference:
        raise ValueError("not a statistic reference in its one spelling")
    return parsed


def _stat_reference(value: str) -> str:
    try:
        parse_stat_reference(value)
    except ValueError:
        raise problem("stat_reference", "Not a statistic reference in its one spelling") from None
    return value


StatRef = Annotated[
    str,
    AfterValidator(_stat_reference),
    WithJsonSchema({"type": "string", "pattern": STAT_REFERENCE_RE.pattern, **DATA_MARK}),
]
"""A release-scoped reference to a catalogue statistic (§8.1, D272); its pointer's tokens can
be values from data, so it is data (A6)."""


__all__ = [
    "STAT_REFERENCE_RE",
    "Analysed",
    "AnalysedCounts",
    "AnalysedVariable",
    "AnalysisRef",
    "Chart",
    "CohortCount",
    "CohortRef",
    "Derivation",
    "Disclosure",
    "Issuance",
    "PackVersion",
    "Pep440",
    "Population",
    "Readback",
    "ReleaseRef",
    "ResultEnvelope",
    "Source",
    "StatRef",
    "StatReference",
    "Values",
    "parse_stat_reference",
    "stat_reference",
]

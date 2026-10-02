"""Canonical forms and ids: phase 1 finished, phase 2's shape (SPEC §7.6; D281–D284, D287).

Resolution (``resolve``) does steps 1 to 6 of phase 1 and removes duplicates as step 8 does,
bottom up, so its tree is the fixpoint of steps 4 to 6 (D212). What is left is done here:

- step 7, each node with exactly the members of §7.6's table (``resolved.document``);
- step 8, every order-insensitive collection sorted, innermost first, by its members' RFC 8785
  serialisation compared as UTF-16 code units, those serialised alike kept once: ``values``,
  ``ids``, the value lists of ``covered.scope``, and the clauses of ``all``, ``any`` and every
  ``where`` (``canonical_clause``). Resolution kept one of each clause step 8 would find alike,
  so sorting removes no clause and the form it gives is the fixpoint (D281);
- step 9: no name, note or ``drafted_by`` reaches a form, which holds descriptor ids and
  manifest hashes only.

A cohort's canonical form maps its release's manifest hash to its clause tree: its top-level
``all``, a single member unwrapped. Its id hashes ``{"cohort", "unit", "semantics_version",
"disclosure", "packs"}`` (D282): the unit table's id, the core's ``SEMANTICS_VERSION``, the
effective ``min_cell_count`` over the floor and the dataset's setting, and the results version of
each pack whose leaves its expansion holds or that its dataset lists. The computation id hashes
the same object without ``disclosure``. Each top-level clause has the leaf key of its canonical
form, and each leaf as written the keys of the clauses it became part of (``source.leaves``).

The packs involved in a cohort run their caveat rules on its canonical form (D287); what they
raise is static, like ``UNCONFIRMED_SEMANTICS``, and joins its caveats.

Phase 2 waits for the registry (M3): until then views are syntax-checked by the loader and
reported unchecked, with no id (D284). ``ViewIdentity`` is the canonical view and its result and
computation ids from their parts, as M3 will fill them.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, cast

from pydantic import JsonValue

from aibi.core.engine.data import Release
from aibi.core.engine.ids import derivation_id, leaf_key, sorted_unique
from aibi.core.engine.resolve import Label, PackView, ResolvedCohort, pack_failed, resolve
from aibi.core.engine.resolved import (
    RAll,
    RAny,
    RClause,
    RCovered,
    RExists,
    RIds,
    RKnown,
    RNot,
    RValue,
    document,
)
from aibi.core.schema.caveats import Severity
from aibi.core.schema.document import Document
from aibi.core.schema.jsonio import canonical, pointer
from aibi.core.schema.loading import as_written
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.schema.params import Position
from aibi.core.schema.refusals import Refusal, RefusalCode, finish_refusals
from aibi.core.schema.results import PackVersion, ReleaseRef
from aibi.core.schema.semantics import SEMANTICS_VERSION

# --- Step 8 ------------------------------------------------------------------------------------


def canonical_clause(node: RClause) -> JsonValue:
    """A resolved clause in canonical form: exactly §7.6's members, and every order-insensitive
    collection sorted by canonical serialisation, innermost first, without duplicates."""
    if isinstance(node, RValue):
        written = cast(dict[str, JsonValue], document(node))
        if "values" in written:
            written["values"] = sorted_unique(cast(list[JsonValue], written["values"]))
        return written
    if isinstance(node, RExists):
        written = cast(dict[str, JsonValue], document(replace(node, where=())))
        written["where"] = _clauses(node.where)
        return written
    if isinstance(node, RCovered):
        written = cast(dict[str, JsonValue], document(node))
        scope = cast(dict[str, list[JsonValue]] | None, written.get("scope"))
        if scope is not None:
            written["scope"] = {column: sorted_unique(values) for column, values in scope.items()}
        return written
    if isinstance(node, RIds):
        written = cast(dict[str, JsonValue], document(node))
        written["ids"] = sorted_unique(cast(list[JsonValue], written["ids"]))
        return written
    if isinstance(node, RAll):
        return {"all": _clauses(node.members)}
    if isinstance(node, RAny):
        return {"any": _clauses(node.members)}
    if isinstance(node, RNot):
        return {"not": canonical_clause(node.member)}
    if isinstance(node, RKnown):
        return {"known": canonical_clause(node.member)}
    return {"unknown": canonical_clause(node.member)}


def _clauses(nodes: Sequence[RClause]) -> list[JsonValue]:
    """Clauses sorted; resolution kept one of each clause step 8 finds alike (D281), so a
    duplicate here is resolution's error, and the form would not be the fixpoint."""
    written = sorted_unique(canonical_clause(node) for node in nodes)
    if len(written) != len(nodes):
        raise RuntimeError("resolution kept clauses that step 8 finds alike")
    return written


def as_document(clause: JsonValue, datasets: Mapping[str, str]) -> JsonValue:
    """A canonical clause as a document writes it (D281): each unit key's manifest hash as the
    dataset ``datasets`` maps it to, and every string starting with ``$`` written ``$$…``, since
    a document's strings that start with ``$`` are parameter references (§7.1). A cohort of these
    clauses over those datasets' releases canonicalises to the same form."""
    if isinstance(clause, str):
        return "$" + clause if clause.startswith("$") else clause
    if isinstance(clause, list):
        return [as_document(item, datasets) for item in clause]
    if isinstance(clause, dict):
        found = {key: as_document(member, datasets) for key, member in clause.items()}
        manifest = clause.get("dataset")
        if isinstance(manifest, str) and "key" in clause and manifest in datasets:
            found["dataset"] = datasets[manifest]
        return found
    return clause


# --- Cohorts -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class CohortIdentity:
    """What a cohort id hashes (§7.6, D282). The objects it hashes are written down when it is
    built, and its ids computed then, so that nothing done to what it was built from, to its
    ``form`` or to what ``hashed`` gives out changes them; ``hashed`` gives a new copy."""

    form: Mapping[str, JsonValue]
    """The canonical cohort: each of its datasets' manifest hashes, and its clause tree."""
    unit: str
    packs: Mapping[str, int]
    """Each pack whose leaves the expansion holds or that a dataset lists: its results
    version."""
    disclosure: int | None
    """The effective ``min_cell_count``: the largest of the floor and the datasets' settings."""
    id: str = field(init=False)
    computation_id: str = field(init=False)
    """The id of the same object without ``disclosure``."""
    _texts: tuple[bytes, bytes] = field(init=False, repr=False, compare=False)
    """The RFC 8785 text of the object each id hashes."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "form", cast(dict[str, JsonValue], _copy(dict(self.form))))
        object.__setattr__(self, "packs", dict(sorted(self.packs.items())))
        found: dict[str, JsonValue] = {
            "cohort": cast(dict[str, JsonValue], _copy(dict(self.form))),
            "unit": self.unit,
            "semantics_version": SEMANTICS_VERSION,
            "packs": dict(self.packs),
        }
        without = dict(found)
        found["disclosure"] = {"min_cell_count": self.disclosure}
        object.__setattr__(self, "_texts", (canonical(found), canonical(without)))
        object.__setattr__(self, "id", derivation_id(found))
        object.__setattr__(self, "computation_id", derivation_id(without))

    def hashed(self, *, disclosure: bool = True) -> dict[str, JsonValue]:
        """The object the id hashes; without ``disclosure``, the computation id's."""
        return cast(dict[str, JsonValue], json.loads(self._texts[0 if disclosure else 1]))


@dataclass(frozen=True)
class RuledCaveat:
    """A caveat code a pack's caveat rule raised for a cohort, with its declared severity."""

    code: str
    severity: Severity
    pack: str


@dataclass(frozen=True)
class CanonicalCohort:
    """A cohort in canonical form, with its id and what outputs carry beside it."""

    name: str
    """Its name in the document: never hashed."""
    resolved: ResolvedCohort
    identity: CohortIdentity
    clauses: tuple[JsonValue, ...]
    """The canonical top-level clauses, as ``resolved.clauses`` orders them."""
    keys: tuple[str, ...]
    """The leaf key of each top-level clause, as ``clauses`` orders them."""
    leaves: Mapping[str, tuple[str, ...]]
    """Each leaf as written, by its pointer into the document as written, and the sorted keys of
    the top-level clauses it became part of; one that became none is left out (§8.1)."""
    release: ReleaseRef
    packs: Mapping[str, PackVersion]
    """Each pack involved, with the version installed and its results version."""
    caveats: tuple[RuledCaveat, ...]
    """What the packs' caveat rules raised, sorted, each once."""
    summaries: Mapping[str, tuple[Segment, ...]] = field(
        default_factory=dict[str, tuple[Segment, ...]]
    )
    """Each pack leaf as written, by pointer, and its pack's summary (§7.3)."""

    @property
    def form(self) -> Mapping[str, JsonValue]:
        return self.identity.form

    @property
    def id(self) -> str:
        return self.identity.id

    @property
    def computation_id(self) -> str:
        return self.identity.computation_id


@dataclass(frozen=True)
class UncheckedView:
    """A view of the document, syntax-checked only until M3 (§15, D284)."""

    position: int
    analysis: str
    status: Literal["unchecked"] = "unchecked"


@dataclass(frozen=True)
class Canonicalisation:
    cohorts: Mapping[str, CanonicalCohort]
    """The cohorts canonicalised, by name; one refused, or that depends on one, is left out."""
    views: tuple[UncheckedView, ...]
    refusals: list[Refusal]


def canonicalise(
    written: Document,
    releases: Mapping[str, Release],
    *,
    labels: Mapping[str, Label],
    registry: PackRegistry | None = None,
    floor: int | None = None,
    positions: Mapping[Position, str] | None = None,
) -> Canonicalisation:
    """Canonicalise a loaded document's cohorts (phase 1) against its releases.

    ``releases`` maps each dataset reference as written to its release, ``labels`` each release's
    manifest hash to its label (``"draft"`` for a session's draft), ``registry`` holds the
    installed packs, ``floor`` is the deployment's ``min_cell_count`` floor, and ``positions``
    are the loader's, so that pointers lead into the document as written."""
    given = positions or {}
    resolution = resolve(written, releases, given, registry=registry)
    refusals = list(resolution.refusals)
    cohorts: dict[str, CanonicalCohort] = {}
    for name, resolved in resolution.cohorts.items():
        found = _cohort(name, resolved, labels, registry, floor, given)
        if isinstance(found, Refusal):
            refusals.append(found)
        else:
            cohorts[name] = found
    views = tuple(
        UncheckedView(index, view.analysis) for index, view in enumerate(written.views or [])
    )
    return Canonicalisation(cohorts, views, finish_refusals(refusals))


def _cohort(
    name: str,
    resolved: ResolvedCohort,
    labels: Mapping[str, Label],
    registry: PackRegistry | None,
    floor: int | None,
    positions: Mapping[Position, str],
) -> CanonicalCohort | Refusal:
    release = resolved.release
    clauses = tuple(canonical_clause(clause) for clause in resolved.clauses)
    tree: JsonValue = clauses[0] if len(clauses) == 1 else {"all": sorted_unique(clauses)}
    form: dict[str, JsonValue] = {release.manifest: tree}
    keys = tuple(leaf_key(clause) for clause in clauses)
    dataset = release.dataset_descriptor
    listed = () if dataset is None else tuple(dataset.fields.packs or ())
    involved = sorted(resolved.packs | set(listed))
    packs: dict[str, PackVersion] = {}
    if involved:
        if registry is None:
            raise RuntimeError("resolution refuses the packs of a cohort that are not installed")
        for pack in registry.listed(involved):
            packs[pack.id] = PackVersion(
                version=pack.manifest.version, results_version=pack.manifest.results_version
            )
    settings = None if dataset is None else dataset.fields.disclosure
    disclosure = _effective(floor, None if settings is None else settings.min_cell_count)
    identity = CohortIdentity(
        form,
        resolved.unit,
        {pack: version.results_version for pack, version in packs.items()},
        disclosure,
    )
    label = labels[release.manifest]
    caveats = _ruled(registry, involved, release, identity.form)
    if caveats is None:
        at, _ = as_written(("cohorts", name, "all"), positions)
        return Refusal(
            code=RefusalCode.PACK_FAILED,
            path=pointer(list(at)),
            message=[
                text("A pack's caveat rule failed, or gave what is not a list of the codes it "),
                text("declares, for cohort "),
                data(name),
            ],
        )
    leaves: dict[str, set[str]] = {}
    for position, indexes in resolved.leaves.items():
        if indexes:
            at, _ = as_written(position, positions)
            leaves.setdefault(pointer(list(at)), set()).update(keys[index] for index in indexes)
    summaries = {
        pointer(list(as_written(position, positions)[0])): summary
        for position, summary in resolved.summaries.items()
    }
    return CanonicalCohort(
        name=name,
        resolved=resolved,
        identity=identity,
        clauses=clauses,
        keys=keys,
        leaves={at: tuple(sorted(found)) for at, found in sorted(leaves.items())},
        release=ReleaseRef(
            dataset=release.dataset,
            label=label,
            manifest=release.manifest,
            status="draft" if label == "draft" else "published",
        ),
        packs=packs,
        caveats=caveats,
        summaries=summaries,
    )


def _effective(floor: int | None, setting: int | None) -> int | None:
    """The effective ``min_cell_count``: the largest of the floor and the setting (§8.4)."""
    given = [k for k in (floor, setting) if k is not None]
    return max(given) if given else None


def _ruled(
    registry: PackRegistry | None,
    involved: Sequence[str],
    release: Release,
    form: Mapping[str, JsonValue],
) -> tuple[RuledCaveat, ...] | None:
    """What the caveat rules of the packs involved raise for a canonical cohort, each code once;
    ``None`` when a rule raises, or gives what is not a list of codes its pack declares (D287).
    Each rule reads a copy of the form of its own, and a view of its own of the release's
    descriptors, copied as it reads them, without its label; none is made for a pack without a
    rule. A ``MemoryError`` is not the pack's failure, and is raised; anything else a rule
    raises, a ``RecursionError`` included, is."""
    if registry is None or not involved:
        return ()
    found: set[RuledCaveat] = set()
    for pack in registry.listed(involved):
        if pack.caveat_rule is None:
            continue
        view = PackView.of(release)
        try:
            given = cast(
                object, pack.caveat_rule(view, cast(dict[str, JsonValue], _copy(dict(form))))
            )
            if not isinstance(given, list | tuple):
                return None
            codes = [
                str.__str__(code)
                for code in cast(Sequence[object], given)
                if isinstance(code, str) and str.__str__(code) in pack.caveat_codes
            ]
            if len(codes) != len(cast(Sequence[object], given)):
                return None
        except MemoryError:
            raise
        except Exception as error:
            pack_failed(pack.id, "caveat rule", error)
            return None
        found.update(RuledCaveat(code, pack.caveat_codes[code], pack.id) for code in codes)
    return tuple(sorted(found, key=lambda caveat: (caveat.code, caveat.pack)))


def _copy(value: JsonValue) -> JsonValue:
    """A copy a pack may change without changing the form."""
    if isinstance(value, dict):
        return {key: _copy(member) for key, member in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


# --- Phase 2 (M3) -------------------------------------------------------------------------------


@dataclass(frozen=True)
class ViewIdentity:
    """A view's canonical form and its ids, from their parts (§7.6, phase 2; D284).

    ``cohorts`` are in view order; ``default_order`` gives the order of a view that names none.
    ``reference`` is given only for an analysis that declares ``uses_reference``, and
    ``overlap`` only for one that declares ``assumes_independent_groups``."""

    analysis: str
    version: str
    cohorts: tuple[CohortIdentity, ...]
    params: JsonValue
    """The canonical parameters, every default written (M3)."""
    packs: Mapping[str, int]
    """The results version of each pack of the analysis and of its cohorts."""
    disclosure: int | None
    """The effective ``min_cell_count`` over every dataset of the view and the floor."""
    reference: int | None = None
    overlap: bool | None = None

    def __post_init__(self) -> None:
        if self.reference is not None and not 0 <= self.reference < len(self.cohorts):
            raise ValueError("the reference is a position of the view's cohorts")

    @staticmethod
    def default_order(cohorts: Sequence[CohortIdentity]) -> tuple[CohortIdentity, ...]:
        """The cohorts of a view that names none, by id. They are ordered by their ids without
        the disclosure setting, so that raising a floor never reorders them, which would change
        the computation id and the resampling it seeds (§9.3, D284)."""
        return tuple(sorted(cohorts, key=lambda cohort: cohort.computation_id))

    def view(self, *, computation: bool = False) -> dict[str, JsonValue]:
        found: dict[str, JsonValue] = {
            "analysis": {"id": self.analysis, "version": self.version},
            "cohorts": [
                cohort.computation_id if computation else cohort.id for cohort in self.cohorts
            ],
            "params": self.params,
        }
        if self.reference is not None:
            found["reference"] = self.reference
        if self.overlap is not None:
            found["overlap"] = self.overlap
        return found

    def hashed(self, *, disclosure: bool = True) -> dict[str, JsonValue]:
        """The object the result id hashes; without ``disclosure`` at every level, the one the
        computation id hashes."""
        found: dict[str, JsonValue] = {
            "view": self.view(computation=not disclosure),
            "packs": dict(sorted(self.packs.items())),
        }
        if disclosure:
            found["disclosure"] = {"min_cell_count": self.disclosure}
        return found

    @property
    def id(self) -> str:
        return derivation_id(self.hashed())

    @property
    def computation_id(self) -> str:
        return derivation_id(self.hashed(disclosure=False))


__all__ = [
    "CanonicalCohort",
    "Canonicalisation",
    "CohortIdentity",
    "RuledCaveat",
    "UncheckedView",
    "ViewIdentity",
    "as_document",
    "canonical_clause",
    "canonicalise",
]

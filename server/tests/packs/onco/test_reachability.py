"""The closure guard (D409, D436): no import reaches ``discover`` unless the table of the path
values, ``study.PATH_SITES``, is closed and is the table ``discover`` consults.

M4.2a-1a-paths' PR A had ``discover`` check every path value outside the clinical role (phases 9
and 10) and named the sites in ``study.PATH_SITES``; this PR (B) replaces its seed-key check with
the closure check. ``closed(pack, study)`` is true while the pack has no importer; with one wired,
only when ``closure(study)`` finds nothing wrong, by these legs, each a failure of its own:

- **pairs** and **types**: the table's rows are the hand rows of ``paths_oracle.ROWS`` (the test's
  own statement of each site, written from D409 and the survey, never read from ``study``): its
  ``(key, role)`` pairs, in order, and each row's meta types; so the whole rows.
- **codomain**: ``meta.classify``, run over the guard's own metas, one for each pair of
  ``meta.PAIR_TYPES`` and each resource type of ``meta.RESOURCE_TYPES`` (``derived_heads``: every
  type the pack's key tables name, never the oracle's lists; the clinical types of
  ``meta.CLINICAL`` stay out, which would enter it), with the study meta, has exactly the types of
  those tables and ``STUDY`` for its codomain. The cases are generated over that codomain.
- **anchor**: the type facts are held to a derivation outside ``paths_oracle.py``, and the roles and
  keys to a structure, by statements of their own (``STATEMENTS``, each pinned by self-checks, one
  for each direction of an equality): ``pairs`` and ``resources``, the oracle's ``PAIRS`` and
  ``RESOURCES`` are exactly the key tables' (clinical pairs left out); ``datatypes``, its
  ``DATA_TYPES`` are those tables' types without the clinical ones; ``holds``, the types whose
  ``meta.MANDATORY`` keys hold ``data_filename`` are the codomain without ``STUDY``; ``data``, the
  ``data_filename`` row's types are those; ``listing``, the ``listing:meta`` row's are exactly the
  codomain and the clinical types; ``pd``, the PD row's are exactly the type the discrete CNA pair
  classifies to (V:1250, the one fact the tables cannot state); ``tags``, the ``tags_file`` row
  exists and its types are among the types whose keys lack ``data_filename`` (a subset: the tables
  do not say which of them carries ``tags_file``, but ``holds`` makes that set ``STUDY`` alone; that
  the row reads at all is the ``refuses`` leg's); ``roles``, the rows whose key holds no ``:`` (a
  meta file's key never does; the listing rows' are ``listing:...`` and ``isdir:...``) are exactly
  the rows of the checked roles (``CHECKED``, the data and tags roles), and there is a row of each.
  A type dropped from the oracle and the table alike, a row swapped in both, a role relabelled in
  both, ``CHECKED`` emptied, the tags key renamed in both, are red here though another row still
  covers them. What the anchor does **not** hold: which of the two checked roles a key has beyond
  that structure, the spelling of the keys ``pd_annotations_filename`` and ``tags_file`` (the
  reference's, in no table of ``meta``), and the refusals' codes, verbs and wording
  (``guard_expected``). Those are held by PR A: ``test_paths.ROLES`` writes each checked site's key
  literally and what it refuses, and ``test_path_sites_is_the_survey`` lists the keys literally.
  **When ``meta``'s tables change legitimately** (a new type, a type made clinical, a new
  mandatory key, a type without ``data_filename``) the anchor goes red until the hand facts are
  stated again from the new survey: ``paths_oracle.py`` (``DATA_TYPES``, ``PAIRS``, ``RESOURCES``,
  ``ROWS``, and ``EVERY_KEY`` for a new mandatory key), and ``anchored`` for a type without
  ``data_filename`` (its ``holds`` statement); that is a moved pin, re-surveyed (D409), and the
  edit is acceptable there. M4.2a-1b changes none of these tables.
- **refused**, **names**, **ignored**: for each checked hand row (``data``, ``tags``) and each type
  of the codomain, a study whose meta file of that type holds a member of Reads(S) at the row's
  key, every other site clean (``paths_oracle.guard_case``), is refused where the hand row reads
  that type, with the code of the row's role and a message naming the meta file and the row's
  key, and accepted where it does not (``paths_oracle.guard_expected``).
- **refuses**: every checked row has a refuse case.
- **deletion**: deleting a checked row from ``study.PATH_SITES`` turns at least one of its refuse
  cases accepted, so ``discover`` consults the row (a table it never reads fails here); and
  **declared**: each declared row (``listing``, ``case lists``) has its case, accepted
  (``paths_oracle.declared_case``: a meta file of a type never imported, a ``case_lists``
  directory, exit 3 (19)), and deleting it changes no case's outcome.
  The contract is that ``discover`` reads ``study.PATH_SITES`` at call time, each row's types
  included (``study.py``, its loop over the table inside ``discover``): a refactor that binds the
  table, or a row's types, at import, though it behaves the same, fails here by that contract (the
  self-checks that edit a row's types rely on it). The table is restored in a ``finally``.

That ``discover`` reads each row's types is held by the cases (a type it skips fails the refused
leg, a type it adds the ignored one); that the table's types are the hand rows', by the types
leg. The guard's self-checks are tests of the guard: tables and stand-ins it must refuse, and each
leg dropped in turn against a mutant built to pass every other leg. **M4.2a-1b**, which wires
the importer and is stacked on this PR, does not touch ``closed``, ``closure``, ``anchored`` or
``paths_oracle.py``: its wiring passes them as they are, and this
module's ``test_discover_not_reachable_before_paths`` then runs the whole closure on the pack.
The guard holds the ``study`` module, not that ``PACK.importer`` calls ``study.discover``: 1b
must add its own end-to-end test that an import through ``PACK.importer`` refuses a member of
Reads(S) at a ``data_filename``, and keeps ``discover``'s ``(reader, source, limits)`` signature
and its refusal wording, so that ``paths_oracle.py`` stays as it is. The anchor's independence
is the pack's own: it holds each type fact to ``meta.PAIR_TYPES``, ``RESOURCE_TYPES``,
``MANDATORY`` and ``CLINICAL``, so a bug those tables share with ``discover`` is not caught
here (the paths corpus and its live reference cells are the anchor for that).
"""

import dataclasses
import importlib
import importlib.util
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, NamedTuple

import pytest

from aibi.core.schema.output import TextSegment, text
from aibi.core.schema.pack_api import Refused
from aibi.core.schema.refusals import Refusal

MODULE = "aibi.packs.onco"
HERE = Path(__file__).resolve().parent
LEGS = frozenset(
    {
        "pairs",
        "types",
        "codomain",
        "anchor",
        "refused",
        "names",
        "ignored",
        "refuses",
        "deletion",
        "declared",
    }
)
_HELPERS: dict[str, ModuleType] = {}


def helper(name: str) -> ModuleType:
    """A helper module of this directory (``paths_oracle``, ``clinical_grid``), loaded by its path
    once (test modules cannot import one another, and none may load one at import)."""
    if name not in _HELPERS:
        spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _HELPERS[name] = module
    return _HELPERS[name]


class Verdict(NamedTuple):
    failure: str | None
    """The first leg that fails, as ``"<leg>: <why>"``; ``None`` when the closure holds."""
    cases: int
    refuse: int


@contextmanager
def without(study: Any, key: str) -> Iterator[None]:
    """``study.PATH_SITES`` without its row ``key``, restored in a ``finally``."""
    saved = study.PATH_SITES
    study.PATH_SITES = tuple(site for site in saved if getattr(site, "key", None) != key)
    try:
        yield
    finally:
        study.PATH_SITES = saved


DISCRETE_CNA = "COPY_NUMBER_ALTERATION:DISCRETE"
"""The pair of the discrete CNA validator (V:1250), whose meta type the PD row reads."""
STATEMENTS = ("pairs", "resources", "datatypes", "holds", "data", "listing", "pd", "tags", "roles")
"""The anchor's statements, in the order they are checked; each has a self-check that pins it."""


def derived_heads(meta: Any) -> dict[str, str]:
    """A meta file's head for each non-clinical pair of ``meta.PAIR_TYPES`` and each resource type
    of ``meta.RESOURCE_TYPES``, by the label of its entry in the table."""
    heads = {}
    for label, kind in meta.PAIR_TYPES.items():
        if kind not in meta.CLINICAL:
            alteration, datatype = label.split(":", 1)
            heads[label] = f"genetic_alteration_type: {alteration}\ndatatype: {datatype}\n"
    for resource in meta.RESOURCE_TYPES:
        heads[f"resource_type:{resource}"] = f"resource_type: {resource}\n"
    return heads


def tabled_types(meta: Any) -> set[str]:
    """The non-clinical types the pack's key tables name, with ``STUDY``."""
    named = {*meta.PAIR_TYPES.values(), *meta.RESOURCE_TYPES.values()}
    return named - meta.CLINICAL | {meta.STUDY}


def anchored(
    oracle: Any,
    hand: Sequence[Any],
    codomain: set[str],
    labelled: Mapping[str, str],
    statements: frozenset[str] = frozenset(STATEMENTS),
) -> str | None:
    """Why a hand fact is not what ``meta`` says, else ``None``: the oracle's pairs, resources and
    data types, and the hand rows' types, are held to the pack's key tables, ``codomain`` and the
    mandatory keys, and the rows' roles to the structure of their keys, each by its own statement
    (``STATEMENTS``); ``labelled`` is the type each ``PAIR_TYPES`` label classifies to."""
    meta = importlib.import_module(MODULE + ".meta")
    study_type: str = oracle.STUDY
    holds = {kind for kind in codomain if "data_filename" in meta.MANDATORY.get(kind, ())}
    lacks = codomain - holds
    data = codomain - {study_type}
    types = {row.key: row.types for row in hand}
    tagged = types.get("tags_file")
    bare = {row.key for row in hand if ":" not in row.key}
    read = {row.key for row in hand if row.role in oracle.CHECKED}
    roles = {row.role for row in hand if row.key in bare}
    pairs = sorted(
        (*label.split(":", 1), kind)
        for label, kind in meta.PAIR_TYPES.items()
        if kind not in meta.CLINICAL
    )
    found = {
        "pairs": (sorted(oracle.PAIRS) == pairs, "the oracle's pairs are not the key table's"),
        "resources": (
            dict(oracle.RESOURCES) == dict(meta.RESOURCE_TYPES),
            "the oracle's resource types are not the key table's",
        ),
        "datatypes": (
            set(oracle.DATA_TYPES) == tabled_types(meta) - {meta.STUDY},
            "the oracle's data types are not the key tables' without STUDY",
        ),
        "holds": (
            holds == data,
            "the types whose keys hold data_filename are not the codomain without STUDY",
        ),
        "data": (
            types.get("data_filename") == data,
            "the data_filename row's types are not the codomain without STUDY",
        ),
        "listing": (
            types.get("listing:meta") == codomain | meta.CLINICAL,
            "the listing:meta row's types are not the codomain and the clinical types",
        ),
        "pd": (
            types.get("pd_annotations_filename") == {labelled.get(DISCRETE_CNA)},
            "the pd_annotations_filename row's types are not the discrete CNA pair's type",
        ),
        "tags": (
            tagged is not None and tagged <= lacks,
            "the tags_file row is missing, or reads a type whose keys hold data_filename",
        ),
        "roles": (
            bare == read and set(oracle.CHECKED) == roles == {oracle.DATA, oracle.TAGS},
            "the rows whose key holds no ':' are not exactly the checked rows, of both roles",
        ),
    }
    assert set(found) == set(STATEMENTS)
    for name in STATEMENTS:
        holding, why = found[name]
        if name in statements and not holding:
            return why
    return None


def closure(
    study: Any,
    *,
    classify: Callable[[Mapping[str, str], str], str] | None = None,
    rows: Sequence[Any] | None = None,
    legs: frozenset[str] = LEGS,
    oracle: Any = None,
    statements: frozenset[str] = frozenset(STATEMENTS),
) -> Verdict:
    """Whether ``study`` (a module, or a stand-in with ``PATH_SITES`` and ``discover``) is closed
    under the hand rows, by the legs the module states; ``classify`` and ``rows`` default to the
    pack's ``meta.classify`` and ``paths_oracle.ROWS``, and are given only by the self-checks, as
    are ``oracle`` (a copy of ``paths_oracle`` with other hand facts) and ``statements``."""
    oracle = helper("paths_oracle") if oracle is None else oracle
    meta = importlib.import_module(MODULE + ".meta")
    base: Mapping[str, bytes] = helper("clinical_grid").BASE
    hand: Sequence[Any] = oracle.ROWS if rows is None else rows
    typed: Callable[[Mapping[str, str], str], str] = meta.classify if classify is None else classify
    sites = getattr(study, "PATH_SITES", None)
    if sites is None or not callable(getattr(study, "discover", None)):
        return Verdict("pairs: no PATH_SITES, or no discover", 0, 0)
    table = [
        (getattr(site, "key", None), getattr(site, "role", None), getattr(site, "meta_types", None))
        for site in sites
    ]
    if "pairs" in legs and [(k, r) for k, r, _ in table] != [(row.key, row.role) for row in hand]:
        return Verdict("pairs: the table's (key, role) pairs are not the hand rows'", 0, 0)
    if "types" in legs:
        types = {key: kinds for key, _, kinds in table}
        for row in hand:
            if types.get(row.key) != row.types:
                return Verdict(f"types: the {row.key} row's types are not the hand row's", 0, 0)

    codomain: dict[str, str | None] = {}
    labelled: dict[str, str] = {}
    for label, head in derived_heads(meta).items():
        try:
            kind = typed(oracle.fields(oracle.EVERY_KEY + head), "meta_x.txt")
        except Refused:
            continue
        labelled[label] = kind
        codomain.setdefault(kind, head)
    codomain[oracle.STUDY] = None
    checked = [row for row in hand if row.role in oracle.CHECKED]
    wanted = tabled_types(meta)
    if "codomain" in legs and set(codomain) != wanted:
        return Verdict(f"codomain: {sorted(set(codomain) ^ wanted)} differ", 0, 0)
    if "anchor" in legs:
        why = anchored(oracle, hand, set(codomain), labelled, statements)
        if why is not None:
            return Verdict(f"anchor: {why}", 0, 0)

    cases: list[tuple[dict[str, tuple[str, bytes]], tuple[str, str] | None]] = []
    refusing = 0
    for row in checked:
        refuse = []
        for kind in sorted(codomain):
            entries = oracle.guard_case(base, kind, codomain[kind], row.key)
            want = oracle.guard_expected(row, kind)
            got = oracle.outcome(study, entries)
            cases.append((entries, got))
            where = f"{row.key} in a {kind} meta"
            if want is None:
                if "ignored" in legs and got is not None:
                    return Verdict(f"ignored: {where} is refused; the hand row ignores it", 0, 0)
                continue
            refuse.append(entries)
            if "refused" in legs and got is None:
                return Verdict(f"refused: {where} is accepted; the hand row reads it", 0, 0)
            if "names" in legs and got is not None and got != want:
                return Verdict(f"names: {where} is refused as {got}, not {want}", 0, 0)
        if "refuses" in legs and not refuse:
            return Verdict(f"refuses: the {row.key} row has no refuse case", 0, 0)
        refusing += len(refuse)
        if "deletion" in legs and refuse:
            with without(study, row.key):
                if all(oracle.outcome(study, entries) is not None for entries in refuse):
                    return Verdict(f"deletion: deleting {row.key} turns no case accepted", 0, 0)
    declared = [row for row in hand if row.role not in oracle.CHECKED]
    for row in declared:
        entries = oracle.declared_case(base, row)
        got = oracle.outcome(study, entries)
        cases.append((entries, got))
        if "declared" in legs and got is not None:
            return Verdict(f"declared: the {row.key} case is refused as {got}", 0, 0)
    if "declared" in legs:
        for row in declared:
            with without(study, row.key):
                if [oracle.outcome(study, entries) for entries, _ in cases] != [
                    got for _, got in cases
                ]:
                    return Verdict(f"declared: deleting {row.key} changes an outcome", 0, 0)
    return Verdict(None, len(cases), refusing)


def closed(pack: object, study: object) -> bool:
    """Whether the guard holds: the pack has no importer, or ``study`` is closed (``closure``)."""
    if getattr(pack, "importer", None) is None:
        return True
    return closure(study).failure is None


WIRED = SimpleNamespace(importer=object())


def test_discover_not_reachable_before_paths(onco: ModuleType) -> None:
    """D409 and D436: no import reaches ``discover`` unless the closure holds. Until M4.2a-1b
    wires an importer into ``PACK`` this holds by the first clause; from then on, by the whole
    closure on the pack's ``study``."""
    study = importlib.import_module(MODULE + ".study")
    assert closed(onco.PACK, study)


def test_the_real_table_is_closed() -> None:
    """The pack's ``study``, under a wired importer, is closed: 26 types (25 data types and the
    study meta) for each of the 3 checked rows, 27 of them refuse cases (25, 1 and 1), and a case
    for each of the 3 declared rows."""
    study = importlib.import_module(MODULE + ".study")
    assert closure(study) == Verdict(None, 81, 27)
    assert closed(WIRED, study)


def test_the_guard_refuses_a_stub_or_a_bare_seed_key_set() -> None:
    """A wired importer with no ``PATH_SITES``, an empty one, a stub of it, or the seed rows' keys
    alone (as strings, or as rows with a ``key`` and nothing else, which PR A's check let pass)
    fails; an unwired pack passes whatever its study."""
    study = importlib.import_module(MODULE + ".study")
    keys = [row.key for row in helper("paths_oracle").ROWS]
    assert closed(SimpleNamespace(importer=None), SimpleNamespace())
    assert not closed(WIRED, SimpleNamespace())
    assert not closed(WIRED, SimpleNamespace(PATH_SITES=(), discover=study.discover))
    assert not closed(WIRED, SimpleNamespace(PATH_SITES=keys[1:], discover=study.discover))
    assert not closed(WIRED, SimpleNamespace(PATH_SITES=tuple(keys), discover=study.discover))
    rows = [SimpleNamespace(key=key, sites=("V:0",)) for key in keys]
    assert not closed(WIRED, SimpleNamespace(PATH_SITES=rows, discover=study.discover))
    assert not closed(WIRED, SimpleNamespace(PATH_SITES=study.PATH_SITES))


# --- The guard's self-checks ---------------------------------------------------------------------


class Consulting:
    """A stand-in for ``study`` whose ``discover`` is the pack's, run under
    ``effective(self.PATH_SITES)``, read at call time: the identity is the pack's own behaviour;
    another ``effective`` is a ``discover`` that reads the table otherwise than it shows it.
    ``rewrite`` changes the text of a refusal."""

    def __init__(
        self,
        study: ModuleType,
        effective: Callable[[tuple[Any, ...]], tuple[Any, ...]] = lambda table: table,
        rewrite: Callable[[str], str] | None = None,
    ) -> None:
        self.PATH_SITES: tuple[Any, ...] = study.PATH_SITES
        self.study: Any = study
        self.effective = effective
        self.rewrite = rewrite

    def discover(self, reader: Any, source: Any, limits: Any) -> Any:
        saved = self.study.PATH_SITES
        self.study.PATH_SITES = self.effective(self.PATH_SITES)
        try:
            return self.study.discover(reader, source, limits)
        except Refused as error:
            if self.rewrite is None:
                raise
            (refusal,) = error.refusals
            message = [
                text(self.rewrite(s.text)) if isinstance(s, TextSegment) else s
                for s in refusal.message
            ]
            raise Refused([refusal.model_copy(update={"message": message})]) from None
        finally:
            self.study.PATH_SITES = saved


def edited(table: Sequence[Any], key: str, **changes: Any) -> tuple[Any, ...]:
    """``table`` with the row ``key`` changed, or deleted when no change is given."""
    rows = []
    for row in table:
        if row.key != key:
            rows.append(row)
        elif changes:
            rows.append(dataclasses.replace(row, **changes))
    return tuple(rows)


def narrowed(less: str) -> Callable[[Mapping[str, str], str], str]:
    """``meta.classify``, but a meta file of the type ``less`` is another type (the pack loaded
    at the call, never when the self-checks are listed)."""

    def narrow(found: Mapping[str, str], name: str) -> str:
        kind = importlib.import_module(MODULE + ".meta").classify(found, name)
        return "MUTATION_UNCALLED" if kind == less else kind

    return narrow


def refuses_all(found: Mapping[str, str], name: str) -> str:
    raise Refused([])


Mutant = Callable[[ModuleType, pytest.MonkeyPatch], tuple[Any, dict[str, Any]]]


def on_table(key: str, **changes: Any) -> Mutant:
    """The pack's own ``study`` with its row ``key`` changed or deleted."""

    def make(study: ModuleType, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
        monkeypatch.setattr(study, "PATH_SITES", edited(study.PATH_SITES, key, **changes))
        return study, {}

    return make


def consulting(
    effective: Callable[[tuple[Any, ...]], tuple[Any, ...]] | None = None,
    rewrite: Callable[[str], str] | None = None,
) -> Mutant:
    """A stand-in whose ``discover`` reads the table through ``effective``, or rewrites its
    refusals."""

    def make(study: ModuleType, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
        if effective is None:
            return Consulting(study, rewrite=rewrite), {}
        return Consulting(study, effective, rewrite), {}

    return make


def refuses_case_lists(
    study: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, dict[str, Any]]:
    """A ``discover`` that refuses a study holding a ``case_lists`` directory, which D409 takes
    (exit 3 (19))."""
    stand_in = Consulting(study)
    consult = stand_in.discover

    def discover(reader: Any, source: Any, limits: Any) -> Any:
        if "case_lists" in getattr(reader, "entries", {}):
            refusal = Refusal(code="onco.META_FILE", path=None, message=[text("case lists")])
            raise Refused([refusal])
        return consult(reader, source, limits)

    monkeypatch.setattr(stand_in, "discover", discover)
    return stand_in, {}


def refuses_without_case_lists(
    study: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, dict[str, Any]]:
    """A ``discover`` that, with ``listing:case_lists`` deleted from its table, refuses what it
    accepted, and refuses what it refused as it did: deleting the declared row turns an accepted
    case refused, and no refused case changes."""
    stand_in = Consulting(study)
    consult = stand_in.discover

    def discover(reader: Any, source: Any, limits: Any) -> Any:
        found = consult(reader, source, limits)
        if "listing:case_lists" not in {row.key for row in stand_in.PATH_SITES}:
            refusal = Refusal(code="onco.META_FILE", path=None, message=[text("no case lists")])
            raise Refused([refusal])
        return found

    monkeypatch.setattr(stand_in, "discover", discover)
    return stand_in, {}


def keyed(table: tuple[Any, ...]) -> dict[str, Any]:
    return {row.key: row for row in table}


def skips_timeline(table: tuple[Any, ...]) -> tuple[Any, ...]:
    row = keyed(table).get("data_filename")
    if row is None:
        return table
    return edited(table, "data_filename", meta_types=row.meta_types - {"TIMELINE"})


def tags_as_data(table: tuple[Any, ...]) -> tuple[Any, ...]:
    return edited(table, "tags_file", role="data") if "tags_file" in keyed(table) else table


def pd_widened(table: tuple[Any, ...]) -> tuple[Any, ...]:
    if "pd_annotations_filename" not in keyed(table):
        return table
    types = frozenset({"CNA_DISCRETE", "CNA_DISCRETE_LONG"})
    return edited(table, "pd_annotations_filename", meta_types=types)


def needs_the_listing(table: tuple[Any, ...]) -> tuple[Any, ...]:
    return table if "listing:meta" in keyed(table) else edited(table, "data_filename")


def constant(study: ModuleType, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
    """A table ``discover`` never consults: a copy beside the one it reads."""
    return SimpleNamespace(PATH_SITES=study.PATH_SITES, discover=study.discover), {}


def emptied_in_both(
    study: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, dict[str, Any]]:
    """The tags row emptied in the table and in the hand rows alike (the anchor holds it only to a
    subset): a checked row checking nothing."""
    monkeypatch.setattr(
        study, "PATH_SITES", edited(study.PATH_SITES, "tags_file", meta_types=frozenset())
    )
    rows = [
        dataclasses.replace(row, types=frozenset()) if row.key == "tags_file" else row
        for row in helper("paths_oracle").ROWS
    ]
    return study, {"rows": rows}


def relabelled_in_both(row: str, **changes: str) -> Mutant:
    """The row ``row`` with its ``key`` or ``role`` changed, in the table and the hand rows alike:
    the pairs and types legs agree, and only the anchor's structure of the rows can notice."""

    def make(study: ModuleType, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
        table = tuple(
            dataclasses.replace(r, **changes) if r.key == row else r for r in study.PATH_SITES
        )
        monkeypatch.setattr(study, "PATH_SITES", table)
        rows = [
            dataclasses.replace(r, **changes) if r.key == row else r
            for r in helper("paths_oracle").ROWS
        ]
        return study, {"rows": rows}

    return make


def data_minus_in_both(less: str) -> Mutant:
    """The ``data_filename`` row without the type ``less``, in the table and the hand rows alike:
    another row (the PD row) still reads ``less``, so the union of the rows' types is whole."""

    def make(study: ModuleType, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
        row = keyed(study.PATH_SITES)["data_filename"]
        table = edited(study.PATH_SITES, "data_filename", meta_types=row.meta_types - {less})
        monkeypatch.setattr(study, "PATH_SITES", table)
        rows = [
            dataclasses.replace(r, types=r.types - {less}) if r.key == "data_filename" else r
            for r in helper("paths_oracle").ROWS
        ]
        return study, {"rows": rows}

    return make


def classify_and_rows_narrowed(less: str) -> Mutant:
    """``classify`` finds no ``less``, and the ``data_filename`` and ``listing:meta`` rows lose it
    in the table and the hand rows alike: the rows agree with the codomain they are held to, so
    that only the codomain's own comparison to the data types notices."""

    def classify(found: Mapping[str, str], name: str) -> str:
        kind: str = importlib.import_module(MODULE + ".meta").classify(found, name)
        if kind == less:
            raise Refused([])
        return kind

    def make(study: ModuleType, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
        table = study.PATH_SITES
        for key in ("data_filename", "listing:meta"):
            table = edited(table, key, meta_types=keyed(table)[key].meta_types - {less})
        monkeypatch.setattr(study, "PATH_SITES", table)
        rows = [
            dataclasses.replace(r, types=r.types - {less})
            if r.key in ("data_filename", "listing:meta")
            else r
            for r in helper("paths_oracle").ROWS
        ]
        return study, {"rows": rows, "classify": classify}

    return make


def with_classify(
    classify: Callable[[Mapping[str, str], str], str],
) -> Mutant:
    def make(study: ModuleType, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
        return study, {"classify": classify}

    return make


def hand_copy(**changes: Any) -> SimpleNamespace:
    """``paths_oracle`` as a namespace, with the hand facts named in ``changes`` replaced."""
    return SimpleNamespace(**{**vars(helper("paths_oracle")), **changes})


def in_both(change: Callable[[str, frozenset[str]], frozenset[str]]) -> Mutant:
    """Every row's types changed by ``change(key, types)`` in the table and in the hand rows
    alike."""

    def make(study: ModuleType, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
        table = tuple(
            dataclasses.replace(site, meta_types=change(site.key, site.meta_types))
            for site in study.PATH_SITES
        )
        monkeypatch.setattr(study, "PATH_SITES", table)
        rows = tuple(
            dataclasses.replace(row, types=change(row.key, row.types))
            for row in helper("paths_oracle").ROWS
        )
        return study, {"oracle": hand_copy(ROWS=rows)}

    return make


def swapped_in_both(key: str, kind: str) -> Mutant:
    """The row ``key`` reading the one type ``kind``, in the table and the hand rows alike."""
    return in_both(lambda row, types: frozenset({kind}) if row == key else types)


def row_without_in_both(key: str, less: str) -> Mutant:
    """The row ``key`` without the type ``less``, in the table and the hand rows alike."""
    return in_both(lambda row, types: types - {less} if row == key else types)


def dropped_in_both(less: str) -> Mutant:
    """The type ``less`` dropped everywhere a hand statement of it is made, in the table (every
    row) and the oracle (its rows, pairs, resource types and data types) alike: the pack's own key
    tables still name it."""
    narrow = in_both(lambda key, types: types - {less})

    def make(study: ModuleType, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
        subject, given = narrow(study, monkeypatch)
        oracle = given["oracle"]
        given["oracle"] = hand_copy(
            ROWS=oracle.ROWS,
            PAIRS=tuple(pair for pair in oracle.PAIRS if pair[2] != less),
            RESOURCES={k: v for k, v in oracle.RESOURCES.items() if v != less},
            DATA_TYPES=oracle.DATA_TYPES - {less},
        )
        return subject, given

    return make


def oracle_edited(**changes: Callable[[Any], Any]) -> Mutant:
    """The oracle's hand facts named in ``changes`` replaced (each by a function of the oracle),
    the table as it is."""

    def make(study: ModuleType, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
        oracle = helper("paths_oracle")
        return study, {"oracle": hand_copy(**{name: f(oracle) for name, f in changes.items()})}

    return make


def mandatory_stripped(less: str) -> Mutant:
    """``meta.MANDATORY`` without ``data_filename`` for the type ``less``; the table, built at
    import from the real one, is hard-coded as it was."""

    def make(study: ModuleType, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
        meta = importlib.import_module(MODULE + ".meta")
        stripped = {
            kind: tuple(k for k in keys if not (kind == less and k == "data_filename"))
            for kind, keys in meta.MANDATORY.items()
        }
        monkeypatch.setattr(meta, "MANDATORY", stripped)
        return study, {}

    return make


MUTANTS: dict[str, tuple[str, Mutant]] = {
    "a table discover ignores (constant)": ("deletion", constant),
    "PD row relabelled listing": ("pairs", on_table("pd_annotations_filename", role="listing")),
    "tags row relabelled case lists": ("pairs", on_table("tags_file", role="case lists")),
    "tags row relabelled data": ("pairs", on_table("tags_file", role="data")),
    "PD row relabelled tags": ("pairs", on_table("pd_annotations_filename", role="tags")),
    "a declared row relabelled": ("pairs", on_table("isdir:case_lists", role="listing")),
    "PD row deleted": ("pairs", on_table("pd_annotations_filename")),
    "data row narrowed (no MUTATION)": (
        "types",
        lambda study, monkeypatch: on_table(
            "data_filename",
            meta_types=keyed(study.PATH_SITES)["data_filename"].meta_types - {"MUTATION"},
        )(study, monkeypatch),
    ),
    "PD row widened to DISCRETE_LONG": (
        "types",
        on_table(
            "pd_annotations_filename", meta_types=frozenset({"CNA_DISCRETE", "CNA_DISCRETE_LONG"})
        ),
    ),
    "listing row narrowed (no TIMELINE)": (
        "types",
        lambda study, monkeypatch: on_table(
            "listing:meta",
            meta_types=keyed(study.PATH_SITES)["listing:meta"].meta_types - {"TIMELINE"},
        )(study, monkeypatch),
    ),
    "a vacuous guard (classify finds no type)": ("codomain", with_classify(refuses_all)),
    "classify narrowed (no MUTATION)": ("codomain", with_classify(narrowed("MUTATION"))),
    "classify and the rows narrowed alike (no MUTATION)": (
        "codomain",
        classify_and_rows_narrowed("MUTATION"),
    ),
    "data row without CNA_DISCRETE in the table and the hand rows": (
        "anchor",
        data_minus_in_both("CNA_DISCRETE"),
    ),
    "discover skips TIMELINE": ("refused", consulting(skips_timeline)),
    "discover reads the tags value as data": ("names", consulting(tags_as_data)),
    "a refusal naming another key": (
        "names",
        consulting(
            rewrite=lambda said: said.replace("a pd_annotations_filename", "a data_filename")
        ),
    ),
    "discover reads PD from DISCRETE_LONG too": ("ignored", consulting(pd_widened)),
    "tags row emptied in the table and the hand rows": ("refuses", emptied_in_both),
    "discover consults the listing row": ("declared", consulting(needs_the_listing)),
    "discover refuses the case lists": ("declared", refuses_case_lists),
    "deleting the case lists row turns an accepted case refused": (
        "declared",
        refuses_without_case_lists,
    ),
}
"""Each self-check: what the guard must refuse, and the leg that refuses it first."""

PINNING = {
    "pairs": "a declared row relabelled",
    "types": "listing row narrowed (no TIMELINE)",
    "codomain": "classify and the rows narrowed alike (no MUTATION)",
    "anchor": "data row without CNA_DISCRETE in the table and the hand rows",
    "refused": "discover skips TIMELINE",
    "names": "discover reads the tags value as data",
    "ignored": "discover reads PD from DISCRETE_LONG too",
    "refuses": "tags row emptied in the table and the hand rows",
    "deletion": "a table discover ignores (constant)",
    "declared": "discover consults the listing row",
}
"""For each leg, the mutant that every other leg lets pass."""


STATEMENT_MUTANTS: dict[str, tuple[Mutant, ...]] = {
    "pairs": (
        oracle_edited(
            PAIRS=lambda oracle: tuple(p for p in oracle.PAIRS if p[2] != "GSVA_PVALUES")
        ),
    ),
    "resources": (
        oracle_edited(
            RESOURCES=lambda oracle: {k: v for k, v in oracle.RESOURCES.items() if k != "STUDY"}
        ),
    ),
    "datatypes": (oracle_edited(DATA_TYPES=lambda oracle: oracle.DATA_TYPES - {"GSVA_PVALUES"}),),
    "holds": (mandatory_stripped("SEG"),),
    "data": (data_minus_in_both("CNA_DISCRETE"),),
    "listing": (
        row_without_in_both("listing:meta", "TIMELINE"),
        row_without_in_both("listing:meta", "SAMPLE_ATTRIBUTES"),
        in_both(lambda key, types: types | {"MUTATION_X"} if key == "listing:meta" else types),
    ),
    "pd": (swapped_in_both("pd_annotations_filename", "MUTATION"),),
    "tags": (
        swapped_in_both("tags_file", "MUTATION"),
        relabelled_in_both("tags_file", key="tags_path"),
    ),
    "roles": (
        relabelled_in_both("data_filename", role="listing"),
        oracle_edited(CHECKED=lambda oracle: ()),
    ),
}
"""For each anchor statement, mutants that violate it alone (each of its two directions where it
is an equality): red at the anchor leg, and green with the statement dropped."""

STATEMENT_CASES = [
    (name, n) for name, mutants in STATEMENT_MUTANTS.items() for n in range(len(mutants))
]


def test_the_stand_in_is_the_pack_s_own_behaviour() -> None:
    """The control of the self-checks: ``Consulting`` with no change is closed."""
    study = importlib.import_module(MODULE + ".study")
    assert closure(Consulting(study)) == Verdict(None, 81, 27)


@pytest.mark.parametrize("name", list(MUTANTS))
def test_the_guard_refuses(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each self-check mutant fails the closure, at the leg it is said to; and ``closed`` under a
    wired importer is false."""
    study = importlib.import_module(MODULE + ".study")
    leg, make = MUTANTS[name]
    subject, given = make(study, monkeypatch)
    verdict = closure(subject, **given)
    assert verdict.failure is not None
    assert verdict.failure.split(":")[0] == leg, verdict.failure
    if not given:
        assert not closed(WIRED, subject)


@pytest.mark.parametrize("leg", sorted(LEGS))
def test_each_leg_is_needed(leg: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard with one leg dropped passes the mutant that pins it, which the whole guard
    refuses: no leg can be deleted without a self-check going green."""
    assert set(PINNING) == LEGS
    study = importlib.import_module(MODULE + ".study")
    expected, make = MUTANTS[PINNING[leg]]
    assert expected == leg
    subject, given = make(study, monkeypatch)
    assert (closure(subject, **given).failure or "").split(":")[0] == leg
    assert closure(subject, **given, legs=LEGS - {leg}).failure is None


@pytest.mark.parametrize(("statement", "n"), STATEMENT_CASES)
def test_each_anchor_statement_is_needed(
    statement: str, n: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The anchor with one statement dropped passes each mutant that violates it alone, which the
    whole guard refuses at the anchor leg, and which that statement alone refuses: no statement
    can be deleted without a self-check going green."""
    assert set(STATEMENT_MUTANTS) == set(STATEMENTS)
    study = importlib.import_module(MODULE + ".study")
    subject, given = STATEMENT_MUTANTS[statement][n](study, monkeypatch)
    every = frozenset(STATEMENTS)
    assert (closure(subject, **given).failure or "").split(":")[0] == "anchor"
    assert (closure(subject, **given, statements=frozenset({statement})).failure or "").startswith(
        "anchor"
    )
    assert closure(subject, **given, statements=every - {statement}).failure is None


def test_a_type_dropped_from_the_oracle_and_the_table_alike_is_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For each of the 25 data types in turn: dropped from every row of the table and from the
    oracle's rows, pairs, resource types and data types alike (the pack's key tables still name
    it), the guard is red at the anchor leg, whatever another row still covers."""
    study = importlib.import_module(MODULE + ".study")
    oracle = helper("paths_oracle")
    kinds = sorted(oracle.DATA_TYPES)
    assert len(kinds) == 25
    for kind in kinds:
        with monkeypatch.context() as patch:
            subject, given = dropped_in_both(kind)(study, patch)
            assert kind not in given["oracle"].DATA_TYPES
            verdict = closure(subject, **given)
        assert (verdict.failure or "").split(":")[0] == "anchor", (kind, verdict)


@pytest.mark.parametrize("key", ["pd_annotations_filename", "tags_file"])
def test_a_row_swapped_to_one_type_in_both_is_red(
    key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The PD row and the tags row, each swapped to every one type (the 25 data types and
    ``STUDY``) in the table and the hand rows alike: red at the anchor leg for all but the type the
    row reads (the control, which is closed)."""
    study = importlib.import_module(MODULE + ".study")
    oracle = helper("paths_oracle")
    real = keyed(study.PATH_SITES)[key].meta_types
    red = 0
    for kind in sorted(oracle.DATA_TYPES | {oracle.STUDY}):
        with monkeypatch.context() as patch:
            subject, given = swapped_in_both(key, kind)(study, patch)
            verdict = closure(subject, **given)
        if real == {kind}:
            assert verdict == Verdict(None, 81, 27), kind
        else:
            assert (verdict.failure or "").split(":")[0] == "anchor", (kind, verdict)
            red += 1
    assert red == 25


def test_the_table_is_restored_when_discover_raises() -> None:
    """``without`` restores ``PATH_SITES`` in a ``finally``: a ``discover`` that raises while a
    row is deleted leaves the table as it was."""
    study = importlib.import_module(MODULE + ".study")

    def raising(table: tuple[Any, ...]) -> tuple[Any, ...]:
        if "pd_annotations_filename" not in keyed(table):
            raise RuntimeError("a row is missing")
        return table

    stand_in = Consulting(study, raising)
    table = stand_in.PATH_SITES
    with pytest.raises(RuntimeError):
        closure(stand_in)
    assert stand_in.PATH_SITES is table
    assert study.PATH_SITES is table


def test_the_pack_imports_nothing_yet(onco: ModuleType) -> None:
    """Until M4.2a-1b, ``PACK`` has no importer: discovery is reachable from tests alone."""
    assert onco.PACK.importer is None

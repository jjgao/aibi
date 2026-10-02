"""The pack API (SPEC §10.1).

A pack is a Python package with a manifest that registers implementations of the extension
points below. The core knows packs only through this API: a ``PackRegistry`` built from ``Pack``
objects, which consults only the packs a dataset or document lists. The core never imports a
pack; the server hands the registry the packs it loads.

The extension points are typed here. The core calls each from the milestone that delivers its
feature (M1–M3), so the types some of them exchange (analysis inputs) are placeholders until
then. Raw snapshots and typed tables are the store's
(``aibi.core.store.sources``) and the evaluator's (``aibi.core.engine.data``).
"""

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Literal, NewType, Protocol, cast

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import AfterValidator, Field, JsonValue, StrictInt

from aibi.core.schema.caveats import CORE_SEVERITIES, CaveatCode, Severity
from aibi.core.schema.descriptors import (
    RELEASE_KINDS,
    AnalysisDescriptor,
    ConceptDescriptor,
    Descriptor,
)
from aibi.core.schema.document import Clause, PackKey, PackLeaf
from aibi.core.schema.errors import problem
from aibi.core.schema.ids import (
    CORE_ANALYSIS_FAMILIES,
    MAX_SAFE_INTEGER,
    is_identifier,
    is_pack_code,
)
from aibi.core.schema.jsonio import is_text
from aibi.core.schema.jsonschemas import problems as schema_problems
from aibi.core.schema.limits import MAX_DEPTH, ImportLimits
from aibi.core.schema.output import Output, Segment
from aibi.core.schema.refusals import Refusal
from aibi.core.schema.results import Pep440

if TYPE_CHECKING:
    from aibi.core.engine.data import Table
    from aibi.core.store.build import Layout
    from aibi.core.store.sources import RawSource

# --- The manifest ----------------------------------------------------------------------------


_CLAUSE = r"(?:~=|===|==|!=|<=|>=|<|>)[0-9A-Za-z.*+!_-]{1,64}"
SPECIFIER_RE = re.compile(rf"^{_CLAUSE}(?:,{_CLAUSE}){{0,15}}$")
"""A PEP 440 specifier set without spaces or empty clauses, such as ``>=0.1,<0.2``."""
_LOWER_BOUNDS = ("~=", "===", "==", ">=", ">")


def _specifier(value: str) -> str:
    try:
        specifiers = SpecifierSet(value)
    except InvalidSpecifier:
        raise problem(
            "specifier", "Not a PEP 440 version specifier, such as '>=0.1,<0.2'"
        ) from None
    if not any(specifier.operator in _LOWER_BOUNDS for specifier in specifiers):
        raise problem(
            "specifier",
            "requires_core bounds the core versions from below, such as '>=0.1,<0.2'",
        )
    return value


class PackManifest(Output):
    """``results_version`` is bumped whenever a change to the pack could change outputs; it is
    hashed into ids, so an upgrade that changes nothing changes no id (SPEC §7.6, §10.1)."""

    id: PackKey
    version: Pep440
    results_version: Annotated[StrictInt, Field(ge=1, le=MAX_SAFE_INTEGER)]
    requires_core: Annotated[
        str, Field(max_length=1_200, pattern=SPECIFIER_RE.pattern), AfterValidator(_specifier)
    ]
    """The core versions the pack works with, as a PEP 440 specifier with a lower bound."""


# --- What extension points exchange ----------------------------------------------------------

ConfinedPath = NewType("ConfinedPath", Path)
"""A path the core has confined to the upload area or an import directory (SPEC §14)."""

JsonSchema = Mapping[str, JsonValue]


class ReleaseView(Protocol):
    """What a pack may read of a release: its descriptors, never its data (SPEC §10.1)."""

    @property
    def dataset(self) -> str: ...

    @property
    def manifest(self) -> str:
        """``sha256:<hex>``."""
        ...

    @property
    def label(self) -> int | Literal["draft"]: ...

    @property
    def packs(self) -> Sequence[str]: ...

    @property
    def descriptors(self) -> Mapping[str, Descriptor]:
        """The release's descriptors, by descriptor id."""
        ...


EntryKind = Literal["file", "directory", "symlink", "other", "unconfined"]


@dataclass(frozen=True)
class DirectoryEntry:
    """An entry directly inside a directory, by its own name, before any symlink is followed."""

    name: str
    kind: EntryKind
    """A regular ``file``, confined; a ``directory``; a ``symlink``, never followed; an ``other``
    kind (a FIFO, a socket, a device); or a regular file that could not be confined
    (``unconfined``), which is moved or swapped while it is listed."""
    path: ConfinedPath | None = None
    """For a ``file``, its confined path."""


class SourceReader(Protocol):
    """The only way any importer, the core's or a pack's, reads a file (SPEC §14, D232).

    Every path it takes or returns is confined to the upload area or an import directory, and
    ``read`` reads the file it confined, whatever replaced it since, or refuses."""

    def read(self, path: ConfinedPath, limit: int) -> bytes:
        """The file's bytes, at most ``limit`` of them, or a ``LIMIT_EXCEEDED`` refusal."""
        ...

    def files(self, directory: ConfinedPath) -> Sequence[DirectoryEntry]:
        """What is directly inside a directory, by name; each regular file confined in its
        turn, and no symlink followed."""
        ...

    def location(self, path: ConfinedPath) -> str:
        """The path relative to the root that holds it, as a dataset's ``source`` records it."""
        ...


@dataclass(frozen=True)
class Previous:
    """The names of the release a re-import starts from, so that the *k*-th occurrence of a name
    keeps its id (SPEC §5.1, §12.3, D238). Every importer, the core's or a pack's, passes them to
    ``normalise_names``."""

    tables: tuple[tuple[str, str], ...]
    """(original name, table id) for each table whose ``/label`` has an inference, the name
    being that inference; ordered by name and, within one name, in the order its ids were
    assigned: the id without a collision suffix first, then by suffix ``_<n>`` as a number."""
    columns: Mapping[str, tuple[tuple[str, str], ...]]
    """(source name, column id) of each table's source columns, by table id, in source order."""


@dataclass(frozen=True)
class ImportOptions:
    """What an operator gives an import (SPEC §11.2, §13.1)."""

    dataset: str
    """The dataset's id, which the operator names."""
    reader: SourceReader
    limits: ImportLimits
    at: str
    """RFC 3339: the ``at`` of every curation entry the import writes."""
    name: str | None = None
    """The dataset's name; the source's file name without its extension when not given."""
    original_name: str | None = None
    """An upload's file name, which its stored path does not keep (D234)."""
    previous: Previous | None = None
    """On a re-import, the previous release's names (D238); ``None`` on a first import."""


NoteKind = Literal[
    "skipped_source", "renamed", "not_proposed", "dropped", "unparsed", "gap", "reimported"
]


@dataclass(frozen=True)
class ImportNote:
    """A line of the import report, for the curation queue (D231); it holds no cell values."""

    kind: NoteKind
    subject: str | None
    """The descriptor id or the source name it is about."""
    message: Sequence[Segment]
    count: int | None = None
    rows: Sequence[int] = ()
    """References to rows (counted from 1), at most 5, where a count has them."""


@dataclass(frozen=True)
class ImportResult:
    """What an importer returns: the raw snapshots by source name, each table's layout on
    them, the descriptors with their proposals, and notes for the curation queue."""

    sources: Mapping[str, "RawSource"]
    layouts: Mapping[str, "Layout"]
    descriptors: Sequence[Descriptor]
    notes: Sequence[ImportNote] = ()


RawSnapshot = Mapping[str, "RawSource"]
"""A dataset's raw snapshots, by source name, before parsing (SPEC §12.2)."""

Tables = Mapping[str, "Table"]
"""Typed tables, by table id, rebuilt from raw snapshots (SPEC §12.2)."""


@dataclass(frozen=True)
class Proposal:
    """A proposed descriptor change with its rationale, as a curation proposer returns it (SPEC
    §10.1, §12.3, D249); an importer's own proposals are ``proposed`` fields of its descriptors.

    ``pointer`` names a whole curated field (``/label``, ``/definition``, ``/fields/<name>``,
    ``/extensions/<pack>/<name>``), or is ``""`` for a whole descriptor, whose ``value`` is then
    the descriptor without ``version`` and ``curation``. ``remove`` proposes removing the field or
    the descriptor instead, and then ``value`` is ``None``. It enters the queue by
    ``importer:<pack id>@<pack version>``."""

    descriptor: str
    pointer: str
    value: JsonValue = None
    remove: bool = False
    evidence: str | None = None


class AnalysisInputs(Protocol):
    """Per cohort position, the units with the columns, aggregates and endpoint rows an entry
    requests, materialised by the core (SPEC §10.1). Defined with the registry (M3)."""


class Refused(Exception):  # noqa: N818 - "refused" is the spec's word for a hook's refusal
    """Raised by a hook that refuses its input, such as a leaf compiler (SPEC §7.3)."""

    def __init__(self, refusals: Sequence[Refusal]) -> None:
        super().__init__("; ".join(refusal.code for refusal in refusals))
        self.refusals = tuple(refusals)


@dataclass(frozen=True)
class TranslationNote:
    """A place where a translation changed meaning or could not be exact (SPEC §7.1)."""

    pointer: str
    """A JSON Pointer into the document being translated."""
    message: Sequence[Segment]


# --- The extension points (SPEC §10.1) --------------------------------------------------------


class Importer(Protocol):
    """Imports a source the operator names (SPEC §13.1). ``import`` is a Python keyword."""

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult: ...


class Rebuilder(Protocol):
    """Rebuilds tables a pack importer reshaped, when parse-affecting fields change (§12.2)."""

    def rebuild(self, raw: RawSnapshot, descriptors: Mapping[str, Descriptor]) -> Tables: ...


class Validator(Protocol):
    """A pack's validator (SPEC §10.1). The paths of ``validate_descriptors``'s refusals point
    into the release's descriptors by id, ``/<descriptor id><field pointer>``, and the core
    points them at ``/descriptors/…`` on an import and ``/draft/…`` on a draft change (D246)."""

    def validate_source(self, source: ConfinedPath, result: ImportResult) -> Sequence[Refusal]: ...

    def validate_descriptors(self, release: ReleaseView) -> Sequence[Refusal]: ...


class LeafKind(Protocol):
    """A pack leaf kind (SPEC §7.3). ``compile`` is pure and deterministic, reads no data, and
    returns core clauses with no pack, ``ids`` or ``cohort`` leaves, or raises ``Refused``."""

    @property
    def schema(self) -> JsonSchema:
        """The JSON Schema of the leaf's members."""
        ...

    def compile(
        self, leaf: PackLeaf, release: ReleaseView, pack_version: str
    ) -> Sequence[Clause]: ...

    def summary(self, leaf: PackLeaf) -> Sequence[Segment]:
        """A sentence about the leaf as written, shown as the pack's and kept out of digests."""
        ...


class Translator(Protocol):
    """Translates a document in another format into an aibi document (SPEC §7.1)."""

    def translate(
        self, document: JsonValue
    ) -> tuple[Mapping[str, JsonValue], Sequence[TranslationNote]]: ...


class Analysis(Protocol):
    """A registry entry and its implementation, deterministic as SPEC §9.3 requires."""

    @property
    def entry(self) -> AnalysisDescriptor: ...

    def run(self, inputs: AnalysisInputs) -> Mapping[str, JsonValue]: ...


OntologyValidator = Callable[[str], bool]
"""Whether a code belongs to the ontology system (SPEC §5.4)."""
Proposer = Callable[[ReleaseView], Sequence[Proposal]]
RequirementPredicate = Callable[[ReleaseView], bool]
"""Cited in an analysis's ``requires`` as ``"<pack id>.<name>"`` (SPEC §9.1)."""
Facet = Callable[[ReleaseView], Mapping[str, Sequence[str]]]
"""Catalogue facets of a release: facet name to values (SPEC §11.1)."""
CaveatRule = Callable[[ReleaseView, Mapping[str, JsonValue]], Sequence[str]]
"""Caveat codes for a canonical cohort or view; static, with no access to data."""


@dataclass(frozen=True)
class Pack:
    """A pack's manifest and its implementations of the extension points; each is optional."""

    manifest: PackManifest
    concepts: Sequence[ConceptDescriptor] = ()
    """Registered for every deployment the pack is installed in; ids are ``<pack id>:…``."""
    ontology_systems: Mapping[str, OntologyValidator] = field(
        default_factory=dict[str, OntologyValidator]
    )
    extension_schemas: Mapping[str, JsonSchema] = field(default_factory=dict[str, JsonSchema])
    """A JSON Schema for the pack's extension object, by descriptor kind."""
    importer: Importer | None = None
    rebuilder: Rebuilder | None = None
    validator: Validator | None = None
    proposer: Proposer | None = None
    leaf_kinds: Mapping[str, LeafKind] = field(default_factory=dict[str, LeafKind])
    """By kind, ``<pack id>.<name>``."""
    translators: Mapping[str, Translator] = field(default_factory=dict[str, Translator])
    """By format, ``<pack id>.<name>``."""
    analyses: Sequence[Analysis] = ()
    requirement_predicates: Mapping[str, RequirementPredicate] = field(
        default_factory=dict[str, RequirementPredicate]
    )
    """By name; cited as ``<pack id>.<name>``."""
    facet: Facet | None = None
    caveat_codes: Mapping[str, Severity] = field(default_factory=dict[str, Severity])
    """Every code the pack raises, ``<pack id>.<CODE>``, with its severity (SPEC §8.3)."""
    caveat_rule: CaveatRule | None = None
    wording: Mapping[CaveatCode, str] = field(default_factory=dict[CaveatCode, str])
    """A message template per core code, in the pack's words (SPEC §10.1)."""

    @property
    def id(self) -> str:
        return self.manifest.id


class PackError(ValueError):
    """A pack that cannot be registered, with every problem found."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)


class UnknownPack(LookupError):  # noqa: N818 - raised like KeyError, for a pack id
    """A pack id that no registered pack has."""


def _namespaced(pack: str, name: str) -> bool:
    prefix, dot, rest = name.partition(".")
    return prefix == pack and dot == "." and is_identifier(rest)


def _problems(pack: Pack, core: Version) -> list[str]:
    """Everything wrong with one pack on its own."""
    problems: list[str] = []
    manifest = pack.manifest
    name = manifest.id
    if not SpecifierSet(manifest.requires_core, prereleases=True).contains(core):
        problems.append(f"{name} requires core {manifest.requires_core}, not {core}")
    problems.extend(
        f"{name}: concept {concept.id} is not in its namespace {name}:"
        for concept in pack.concepts
        if not concept.id.startswith(f"{name}:")
    )
    concept_ids = [concept.id for concept in pack.concepts]
    problems.extend(
        f"{name}: concept {concept} is registered twice"
        for concept in sorted({c for c in concept_ids if concept_ids.count(c) > 1})
    )
    problems.extend(
        f"{name}: ontology system name {system!r} is empty, too long or not Unicode text"
        for system in pack.ontology_systems
        if not isinstance(cast(object, system), str)
        or not 0 < len(system) <= 200
        or not is_text(system)
    )
    for label, names in (
        ("leaf kind", pack.leaf_kinds),
        ("translator format", pack.translators),
        ("analysis", [analysis.entry.id for analysis in pack.analyses]),
    ):
        problems.extend(
            f"{name}: {label} {item} is not {name}.<name>"
            for item in names
            if not isinstance(cast(object, item), str) or not _namespaced(name, item)
        )
    analysis_ids = [analysis.entry.id for analysis in pack.analyses]
    if len(set(analysis_ids)) != len(analysis_ids):
        problems.append(f"{name}: an analysis id appears twice")
    problems.extend(
        f"{name}: requirement predicate {predicate!r} is not an identifier"
        for predicate in pack.requirement_predicates
        if not isinstance(cast(object, predicate), str) or not is_identifier(predicate)
    )
    for code, severity in pack.caveat_codes.items():
        if (
            not isinstance(cast(object, code), str)
            or code.partition(".")[0] != name
            or not is_pack_code(code)
        ):
            problems.append(f"{name}: caveat code {code} is not {name}.<CODE>")
        if not isinstance(cast(object, severity), Severity):
            problems.append(f"{name}: caveat code {code} has no severity")
    for analysis in pack.analyses:
        for code in analysis.entry.fields.caveats:
            owner, dot, _ = code.partition(".")
            if (dot and owner == name and code not in pack.caveat_codes) or (
                not dot and code not in CaveatCode
            ):
                problems.append(
                    f"{name}: analysis {analysis.entry.id} cites {code}, which is not declared"
                )
    return problems


class _NotJsonError(ValueError):
    """A schema that is not a JSON value."""


def _plain(value: object, depth: int = 0, inside: frozenset[int] = frozenset()) -> JsonValue:
    """A copy of a JSON value given as mappings and sequences, read-only ones included, made of
    plain dicts and lists. Anything JSON text cannot carry unchanged raises ``_NotJsonError``: a
    key that is not Unicode text, two keys written as the same text, a set, a non-finite number,
    a number beyond ±(2^53 − 1), a value that holds itself, or nesting deeper than JSON text may
    have."""
    if isinstance(value, Mapping | list | tuple):
        identity = id(cast(object, value))
        if identity in inside:
            raise _NotJsonError("a value that holds itself")
        if depth >= MAX_DEPTH:
            raise _NotJsonError(f"nesting deeper than {MAX_DEPTH}")
        within = inside | {identity}
        if isinstance(value, Mapping):
            members = cast(Mapping[object, object], value)
            copied: dict[str, JsonValue] = {}
            for key, member in members.items():
                text = str.__str__(key) if isinstance(key, str) else None
                if text is None or not is_text(text):
                    raise _NotJsonError("a key that is not Unicode text")
                if text in copied:
                    raise _NotJsonError(f"two keys written as {text!r}")
                copied[text] = _plain(member, depth + 1, within)
            return copied
        items = cast(Sequence[object], value)
        return [_plain(item, depth + 1, within) for item in items]
    # Scalars are read, checked and kept as Python's own types, so that a subclass cannot pass
    # the checks as one value and be written as another.
    if value is None:
        return None
    if isinstance(value, bool):
        return value is True
    if isinstance(value, str):
        text = str.__str__(value)
        if not is_text(text):
            raise _NotJsonError("text that is not Unicode")
        return text
    number: int | float
    if isinstance(value, int):
        number = int.__index__(value)
    elif isinstance(value, float):
        number = float.__float__(value)
        if not math.isfinite(number):
            raise _NotJsonError("a number that is not finite")
    else:
        raise _NotJsonError(f"a {type(value).__name__}")
    if abs(number) > MAX_SAFE_INTEGER:
        raise _NotJsonError("a number beyond ±(2^53 - 1)")
    return number


def _copied(value: JsonValue) -> JsonValue:
    """A copy of a JSON value that ``_plain`` made when its pack was registered."""
    if isinstance(value, dict):
        return {key: _copied(member) for key, member in value.items()}
    if isinstance(value, list):
        return [_copied(item) for item in value]
    return value


def _schemas(schemas: Mapping[str, JsonSchema]) -> Mapping[str, JsonSchema]:
    return MappingProxyType(
        {
            kind: cast(dict[str, JsonValue], _copied(cast(JsonValue, schema)))
            for kind, schema in schemas.items()
        }
    )


@dataclass(frozen=True)
class _Registered:
    """An analysis as registered: its entry as it was then, with the pack's implementation."""

    _entry: AnalysisDescriptor
    implementation: Analysis

    @property
    def entry(self) -> AnalysisDescriptor:
        """A copy of the entry as registered, which nothing can change."""
        return self._entry.model_copy(deep=True)

    def run(self, inputs: AnalysisInputs) -> Mapping[str, JsonValue]:
        return self.implementation.run(inputs)


def _snapshot(pack: Pack) -> tuple[Pack, list[str]]:
    """The pack as registered, and what is wrong with its schemas and wordings, which it holds
    without them: later changes to what the caller gave change nothing. Concepts, schemas and
    entries are copied in, and handed out only as copies (``_handed_out``)."""
    name = pack.manifest.id
    problems: list[str] = []
    schemas: dict[str, JsonSchema] = {}
    for kind, schema in pack.extension_schemas.items():
        if kind not in RELEASE_KINDS:
            problems.append(
                f"{name}: extension schema for {kind}, which is not a release descriptor kind"
            )
        if not isinstance(cast(object, schema), Mapping):
            problems.append(f"{name}: the extension schema for {kind} is not a JSON object")
            continue
        try:
            schemas[kind] = cast(dict[str, JsonValue], _plain(schema))
        except _NotJsonError as error:
            problems.append(
                f"{name}: the extension schema for {kind} is not a JSON value: it holds {error}"
            )
            continue
        problems.extend(
            f"{name}: the extension schema for {kind} is refused: {found}"
            for found in schema_problems(schemas[kind])
        )
    wording: dict[CaveatCode, str] = {}
    for code, template in pack.wording.items():
        if code not in CaveatCode:
            problems.append(f"{name}: wording for {code}, which is not a core caveat code")
        given = cast(object, template)
        if not isinstance(given, str) or not given or not is_text(given):
            problems.append(f"{name}: the wording for {code} is not Unicode text")
            continue
        wording[code] = given
    snapshot = replace(
        pack,
        concepts=tuple(concept.model_copy(deep=True) for concept in pack.concepts),
        ontology_systems=MappingProxyType(dict(pack.ontology_systems)),
        extension_schemas=MappingProxyType(schemas),
        leaf_kinds=MappingProxyType(dict(pack.leaf_kinds)),
        translators=MappingProxyType(dict(pack.translators)),
        analyses=tuple(
            _Registered(analysis.entry.model_copy(deep=True), analysis)
            for analysis in pack.analyses
        ),
        requirement_predicates=MappingProxyType(dict(pack.requirement_predicates)),
        caveat_codes=MappingProxyType(dict(pack.caveat_codes)),
        wording=MappingProxyType(wording),
    )
    return snapshot, problems


def _handed_out(pack: Pack) -> Pack:
    """A registered pack as the registry hands it out: its concepts and schemas copied, so that
    changing them changes nothing registered (analyses copy their entries themselves)."""
    return replace(
        pack,
        concepts=tuple(concept.model_copy(deep=True) for concept in pack.concepts),
        extension_schemas=_schemas(pack.extension_schemas),
    )


class PackRegistry:
    """The registered packs, and the extension points the core may consult (SPEC §10.1).

    Lookups that take ``packs`` consult only those packs: a dataset's ``packs``, or those a
    document names. The registry is built once, from every pack at once, and does not change.
    """

    def __init__(self, packs: Iterable[Pack], *, core_version: str) -> None:
        try:
            core = Version(core_version)
        except InvalidVersion:
            raise PackError([f"core version {core_version!r} is not a PEP 440 version"]) from None
        problems: list[str] = []
        snapshots: list[Pack] = []
        for given in packs:
            snapshot, found = _snapshot(given)
            snapshots.append(snapshot)
            problems.extend(found)
        packs = snapshots
        by_id: dict[str, Pack] = {}
        owners: dict[tuple[str, str], str] = {}
        for pack in packs:
            problems.extend(_problems(pack, core))
            problems.extend(
                f"{pack.id}: analysis {analysis.entry.id} cites {code}, which no registered pack "
                "declares"
                for analysis in pack.analyses
                for code in analysis.entry.fields.caveats
                if "." in code
                and code.partition(".")[0] != pack.id
                and not any(code in other.caveat_codes for other in packs)
            )
            if pack.id in by_id:
                problems.append(f"pack {pack.id} is registered twice")
            by_id[pack.id] = pack
            claims = [("ontology system", system) for system in pack.ontology_systems]
            claims += [("concept", concept.id) for concept in pack.concepts]
            for claim in claims:
                if claim in owners and owners[claim] != pack.id:
                    problems.append(
                        f"{claim[0]} {claim[1]} is registered by {owners[claim]} and {pack.id}"
                    )
                owners.setdefault(claim, pack.id)
        if problems:
            raise PackError(problems)
        self._packs = MappingProxyType(dict(sorted(by_id.items())))
        self._systems = {system: pack for pack in packs for system in pack.ontology_systems}
        self._analyses = {
            analysis.entry.id: analysis for pack in packs for analysis in pack.analyses
        }

    # --- Packs ---

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._packs)

    def pack(self, pack_id: str) -> Pack:
        """The pack as registered, with copies of its concepts and schemas."""
        return _handed_out(self._registered(pack_id))

    def _registered(self, pack_id: str) -> Pack:
        try:
            return self._packs[pack_id]
        except KeyError:
            raise UnknownPack(pack_id) from None

    def listed(self, packs: Iterable[str]) -> list[Pack]:
        """The listed packs, in pack id order, as ``pack`` hands them out; an unregistered one
        raises ``UnknownPack``."""
        return [_handed_out(pack) for pack in self._listed(packs)]

    def _listed(self, packs: Iterable[str]) -> list[Pack]:
        return [self._registered(pack_id) for pack_id in sorted(set(packs))]

    # --- Extension points consulted for every registered pack ---

    def concepts(self) -> list[ConceptDescriptor]:
        """Copies of every pack's concepts, in id order."""
        return sorted(
            (
                concept.model_copy(deep=True)
                for pack in self._packs.values()
                for concept in pack.concepts
            ),
            key=lambda concept: concept.id,
        )

    def ontology_validator(self, system: str) -> OntologyValidator | None:
        pack = self._systems.get(system)
        return None if pack is None else pack.ontology_systems[system]

    # --- Extension points consulted for the packs listed ---

    def extension_schemas(self, kind: str, packs: Iterable[str]) -> dict[str, JsonSchema]:
        """The schema each listed pack has for descriptors of ``kind``, by pack id."""
        return {
            pack.id: cast(
                dict[str, JsonValue], _copied(cast(JsonValue, pack.extension_schemas[kind]))
            )
            for pack in self._listed(packs)
            if kind in pack.extension_schemas
        }

    def validators(self, packs: Iterable[str]) -> list[Validator]:
        return [pack.validator for pack in self._listed(packs) if pack.validator is not None]

    def proposers(self, packs: Iterable[str]) -> list[Proposer]:
        return [pack.proposer for pack in self._listed(packs) if pack.proposer is not None]

    def facets(self, packs: Iterable[str]) -> list[Facet]:
        return [pack.facet for pack in self._listed(packs) if pack.facet is not None]

    def caveat_rules(self, packs: Iterable[str]) -> list[CaveatRule]:
        return [pack.caveat_rule for pack in self._listed(packs) if pack.caveat_rule is not None]

    def wordings(self, code: CaveatCode, packs: Iterable[str]) -> list[tuple[str, str]]:
        """The listed packs' wordings of a core code, in pack id order (SPEC §10.1)."""
        return [
            (pack.id, pack.wording[code]) for pack in self._listed(packs) if code in pack.wording
        ]

    # --- Extension points of one pack ---

    def importer(self, pack_id: str) -> Importer | None:
        return self._registered(pack_id).importer

    def rebuilder(self, pack_id: str) -> Rebuilder | None:
        return self._registered(pack_id).rebuilder

    def leaf_kind(self, kind: str) -> LeafKind | None:
        """The leaf kind ``<pack id>.<name>``, from the pack its namespace names: ``None`` if
        that pack has no such kind, and ``UnknownPack`` if no such pack is registered (A3)."""
        return self._registered(kind.partition(".")[0]).leaf_kinds.get(kind)

    def translator(self, format: str) -> Translator | None:
        """As ``leaf_kind``, for a document format ``<pack id>.<name>``."""
        return self._registered(format.partition(".")[0]).translators.get(format)

    def analysis(self, analysis_id: str) -> Analysis | None:
        """A pack's analysis, as ``leaf_kind`` finds a kind; a core family's are not packs'
        (M3)."""
        family = analysis_id.partition(".")[0]
        if family not in CORE_ANALYSIS_FAMILIES:
            self._registered(family)
        return self._analyses.get(analysis_id)

    def analyses(self) -> list[Analysis]:
        return [self._analyses[analysis_id] for analysis_id in sorted(self._analyses)]

    def requirement_predicate(self, reference: str) -> RequirementPredicate | None:
        """A predicate cited as ``<pack id>.<name>``; as ``leaf_kind`` for an unknown pack."""
        pack_id, _, name = reference.partition(".")
        return self._registered(pack_id).requirement_predicates.get(name)

    def severity(self, code: str) -> Severity | None:
        """The declared severity of a core or pack caveat code: ``None`` for a code no one
        declares, and ``UnknownPack`` for a namespace no registered pack has."""
        if code in CaveatCode:
            return CORE_SEVERITIES[CaveatCode(code)]
        if "." not in code:
            return None
        return self._registered(code.partition(".")[0]).caveat_codes.get(code)


__all__ = [
    "Analysis",
    "AnalysisInputs",
    "CaveatRule",
    "ConfinedPath",
    "DirectoryEntry",
    "EntryKind",
    "Facet",
    "ImportNote",
    "ImportOptions",
    "ImportResult",
    "Importer",
    "JsonSchema",
    "LeafKind",
    "NoteKind",
    "OntologyValidator",
    "Pack",
    "PackError",
    "PackManifest",
    "PackRegistry",
    "Previous",
    "Proposal",
    "Proposer",
    "RawSnapshot",
    "Rebuilder",
    "Refused",
    "ReleaseView",
    "RequirementPredicate",
    "SourceReader",
    "Tables",
    "TranslationNote",
    "Translator",
    "UnknownPack",
    "Validator",
]

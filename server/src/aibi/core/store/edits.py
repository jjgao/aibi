"""Applying a change's edits to a draft's descriptors and tombstones (SPEC §5.1, §12.3, D240,
D245, D248).

Edits are applied in order, to copies, and the first that cannot be applied refuses the whole
change, at ``/edits/<index>``; nothing is written until every edit applies. Each field an edit
gives a value gets an entry by the operator (``asserted``), at the server's time, with the edit's
evidence; the entry keeps the ``inferred`` of the entry it replaces, or takes the inference of the
tombstone it lifts, which is the latest import's (D240), since re-import compares inferences
(§12.3) and an edit must not erase one:

- ``set`` writes a field's value;
- ``remove`` deletes a field, never ``/label``, which is required; a field whose entry has an
  inference leaves a tombstone of it;
- ``confirm`` asserts current values, of the fields named or of every field;
- ``put`` writes a whole descriptor without ``version`` or ``curation``, which the server sets:
  a field equal to the draft's keeps its entry, the others are asserted, and a field it leaves
  out is removed as ``remove`` removes it (over a removed descriptor, as below);
- ``remove_descriptor`` removes a relationship, a coverage, an endpoint or a derived column,
  leaving one tombstone of the descriptor with the inferences of its fields and of the field
  tombstones it had; the dataset, a table and a source column are the source's, which only a
  re-import changes;
- ``accept`` applies an open proposal as the operator's: its value or its removal, for a field
  or a whole descriptor, with the evidence ``Proposal <id>``, never the proposer's name or its
  own rationale, which stay in the proposals table that erasure redacts (D248).

Re-adding a tombstoned field or descriptor lifts its tombstone; re-creating a descriptor by
``put`` turns each inference of its tombstone for a field the new descriptor leaves out into a
field tombstone, as ``put`` over the descriptor would have left one. ``propose`` applies one
proposal the same way but stamps it ``proposed`` by its proposer, to check that it would make a
valid release. Every descriptor an edit touched is validated by its model, its problems pointed at
``/<root>/<descriptor id>/…``; ``check_release``, the pack checks and the data checks are the
caller's.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue

from aibi.core.schema.curation import (
    Accept,
    Confirm,
    Edit,
    PutDescriptor,
    RemoveDescriptor,
    RemoveField,
    SetField,
)
from aibi.core.schema.descriptors import RELEASE_KINDS, Descriptor
from aibi.core.schema.jsonio import JsonError, canonical, escape_token, pointer
from aibi.core.schema.loading import load_descriptor
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.refusals import Refusal, RefusalCode, finish_refusals
from aibi.core.store.appdb import StoredProposal
from aibi.core.store.tombstones import Tombstone, ordered

Json = dict[str, JsonValue]
Where = tuple[str | int, ...]
_FORMS = ("/label", "/definition", "/fields/<name>", "/extensions/<pack>/<name>")


class EditRefused(Exception):  # noqa: N818 - the spec's word
    """A change refused, with every refusal of the first stage that failed (D246)."""

    def __init__(self, refusals: Sequence[Refusal]) -> None:
        super().__init__("; ".join(f"{refusal.code} at {refusal.path}" for refusal in refusals))
        self.refusals = tuple(finish_refusals(list(refusals)))


@dataclass(frozen=True)
class Applied:
    descriptors: tuple[Descriptor, ...]
    """Every descriptor of the draft, by id."""
    tombstones: tuple[Tombstone, ...]
    touched: tuple[tuple[str, str], ...]
    """(descriptor id, pointer) of each field or descriptor (``""``) an edit changed."""
    accepted: tuple[int, ...]
    """The proposals accepted, in the order of the edits."""


def _refused(code: RefusalCode, where: Where, *message: Segment | str) -> EditRefused:
    segments = [text(part) if isinstance(part, str) else part for part in message]
    return EditRefused([Refusal(code=code, path=pointer(where), message=segments)])


def _tokens(written: str) -> list[str]:
    return [token.replace("~1", "/").replace("~0", "~") for token in written.split("/")[1:]]


def _curated(where: Where, written: str) -> list[str]:
    """The tokens of a curated field's pointer; any other pointer is refused."""
    tokens = _tokens(written)
    if (
        tokens in (["label"], ["definition"])
        or (len(tokens) == 2 and tokens[0] == "fields")
        or (len(tokens) == 3 and tokens[0] == "extensions")
    ):
        return tokens
    raise EditRefused(
        [
            Refusal(
                code=RefusalCode.INVALID_VALUE,
                path=pointer(where),
                message=[text("Not the pointer of a curated field: "), data(written)],
                alternatives=[text(form) for form in _FORMS],
            )
        ]
    )


def _value(descriptor: Json, tokens: Sequence[str]) -> tuple[bool, JsonValue]:
    """Whether the field has a value, and the value."""
    if tokens[0] in ("label", "definition"):
        return (tokens[0] in descriptor, descriptor.get(tokens[0]))
    if tokens[0] == "fields":
        members = cast(Json, descriptor.get("fields", {}))
        return (tokens[1] in members, members.get(tokens[1]))
    extensions = cast(dict[str, Json], descriptor.get("extensions", {}))
    members = extensions.get(tokens[1], {})
    return (tokens[2] in members, members.get(tokens[2]))


def _write(descriptor: Json, tokens: Sequence[str], value: JsonValue) -> None:
    if tokens[0] in ("label", "definition"):
        descriptor[tokens[0]] = value
    elif tokens[0] == "fields":
        cast(Json, descriptor.setdefault("fields", {}))[tokens[1]] = value
    else:
        extensions = cast(dict[str, Json], descriptor.setdefault("extensions", {}))
        extensions.setdefault(tokens[1], {})[tokens[2]] = value


def _delete(descriptor: Json, tokens: Sequence[str]) -> None:
    if tokens[0] in ("label", "definition"):
        del descriptor[tokens[0]]
    elif tokens[0] == "fields":
        del cast(Json, descriptor["fields"])[tokens[1]]
    else:
        extensions = cast(dict[str, Json], descriptor["extensions"])
        del extensions[tokens[1]][tokens[2]]
        if not extensions[tokens[1]]:
            del extensions[tokens[1]]
        if not extensions:
            del descriptor["extensions"]


def remove_field(descriptor: Json, written: str) -> None:
    """Delete a curated field's value from a written descriptor, and an extension object it
    leaves empty; its curation entry is the caller's."""
    _delete(descriptor, _tokens(written))


def curated_pointers(descriptor: Json) -> list[str]:
    """The pointers of a written descriptor's fields that have values, sorted (§5.1)."""
    found = [name for name in ("label", "definition") if name in descriptor]
    pointers = [f"/{name}" for name in found]
    for name in cast(Json, descriptor.get("fields", {})):
        pointers.append("/fields/" + escape_token(name))
    for pack, members in cast(dict[str, Json], descriptor.get("extensions", {})).items():
        for name in members:
            pointers.append(f"/extensions/{escape_token(pack)}/{escape_token(name)}")
    return sorted(pointers)


def _copy(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: _copy(member) for key, member in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


class _Draft:
    def __init__(
        self,
        descriptors: Sequence[Descriptor],
        tombstones: Sequence[Tombstone],
        *,
        status: str,
        by: str,
        at: str,
    ) -> None:
        self.given = {descriptor.id: descriptor for descriptor in descriptors}
        self.written: dict[str, Json] = {}
        self.removed: set[str] = set()
        self.tombstones = {tombstone.key: tombstone for tombstone in tombstones}
        self.status = status
        self.by = by
        self.at = at
        self.touched: set[tuple[str, str]] = set()
        self.accepted: list[int] = []

    def get(self, identifier: str) -> Json | None:
        if identifier in self.removed:
            return None
        if identifier not in self.written:
            found = self.given.get(identifier)
            if found is None:
                return None
            self.written[identifier] = cast(Json, found.model_dump(mode="json"))
        return self.written[identifier]

    def existing(self, identifier: str, where: Where) -> Json:
        found = self.get(identifier)
        if found is None:
            raise _refused(
                RefusalCode.UNKNOWN_DESCRIPTOR,
                where,
                "The draft has no descriptor ",
                data(identifier),
            )
        return found

    def entry(self, previous: Json | None, lifted: Tombstone | None, evidence: str | None) -> Json:
        """A new entry by the edit's author, keeping the inference it replaces or lifts."""
        entry: Json = {"status": self.status, "by": self.by, "at": self.at}
        if evidence:
            entry["evidence"] = evidence
        if previous is not None and "inferred" in previous:
            entry["inferred"] = _copy(previous["inferred"])
        elif lifted is not None:
            entry["inferred"] = _copy(lifted.inferred)
        return entry

    def curation(self, descriptor: Json) -> Json:
        return cast(Json, descriptor.setdefault("curation", {}))

    # --- The edits -------------------------------------------------------------------------------

    def set(
        self, where: Where, identifier: str, written: str, value: JsonValue, evidence: str | None
    ) -> None:
        tokens = _curated((*where, "pointer"), written)
        descriptor = self.existing(identifier, (*where, "descriptor"))
        curation = self.curation(descriptor)
        previous = cast(Json | None, curation.get(written))
        lifted = self.tombstones.pop((identifier, written), None)
        _write(descriptor, tokens, _copy(value))
        curation[written] = self.entry(previous, lifted, evidence)
        self.touched.add((identifier, written))

    def remove(self, where: Where, identifier: str, written: str) -> None:
        tokens = _curated((*where, "pointer"), written)
        if tokens == ["label"]:
            raise _refused(
                RefusalCode.INVALID_VALUE,
                (*where, "pointer"),
                "A descriptor's label is required: set it instead",
            )
        descriptor = self.existing(identifier, (*where, "descriptor"))
        if not _value(descriptor, tokens)[0]:
            raise _refused(
                RefusalCode.INVALID_VALUE, (*where, "pointer"), "The field has no value to remove"
            )
        self._drop(identifier, descriptor, written, tokens)
        self.touched.add((identifier, written))

    def _drop(self, identifier: str, descriptor: Json, written: str, tokens: Sequence[str]) -> None:
        _delete(descriptor, tokens)
        entry = cast(Json | None, self.curation(descriptor).pop(written, None))
        if entry is not None and "inferred" in entry:
            version = cast(int, descriptor["version"])
            self.tombstones[(identifier, written)] = Tombstone(
                identifier, written, entry["inferred"], version
            )

    def confirm(
        self, where: Where, identifier: str, pointers: Sequence[str] | None, evidence: str | None
    ) -> None:
        descriptor = self.existing(identifier, (*where, "descriptor"))
        curation = self.curation(descriptor)
        named = list(pointers) if pointers is not None else sorted(curation)
        for index, written in enumerate(named):
            at = (*where, "pointers", index) if pointers is not None else (*where,)
            tokens = _curated(at, written)
            if not _value(descriptor, tokens)[0]:
                raise _refused(
                    RefusalCode.INVALID_VALUE,
                    at,
                    "The field has no value to confirm: ",
                    data(written),
                )
            curation[written] = self.entry(cast(Json | None, curation.get(written)), None, evidence)
            self.touched.add((identifier, written))

    def put(self, where: Where, given: JsonValue, evidence: str | None) -> None:
        at = (*where, "descriptor")
        if not isinstance(given, dict):
            raise _refused(RefusalCode.WRONG_TYPE, at, "A descriptor is a JSON object")
        for member, why in (
            ("version", "The server sets a descriptor's version (D243)"),
            ("curation", "The server sets curation entries (§5.1)"),
        ):
            if member in given:
                raise _refused(RefusalCode.INVALID_VALUE, (*at, member), why)
        identifier, kind = given.get("id"), given.get("kind")
        if not isinstance(identifier, str) or kind not in RELEASE_KINDS:
            raise EditRefused(
                [
                    Refusal(
                        code=RefusalCode.INVALID_VALUE,
                        path=pointer((*at, "kind")),
                        message=[text("A draft holds descriptors of a release's kinds")],
                        alternatives=[text(kind) for kind in RELEASE_KINDS],
                    )
                ]
            )
        _shaped(given, at)
        previous = self.get(identifier)
        whole = self.tombstones.pop((identifier, ""), None) if previous is None else None
        inferences = cast(Json, whole.inferred) if whole is not None else {}
        new = cast(Json, _copy(given))
        if new.get("extensions") == {}:
            del new["extensions"]
        new["version"] = previous["version"] if previous is not None else 1
        old_curation = cast(Json, previous.get("curation", {})) if previous is not None else {}
        curation: Json = {}
        for written in curated_pointers(new):
            tokens = _tokens(written)
            kept = cast(Json | None, old_curation.get(written))
            if previous is not None and kept is not None:
                had, before = _value(previous, tokens)
                if had and _same(before, _value(new, tokens)[1]):
                    curation[written] = kept
                    continue
            lifted = self.tombstones.pop((identifier, written), None)
            if lifted is None and written in inferences:
                lifted = Tombstone(identifier, written, inferences[written], 0)
            curation[written] = self.entry(kept, lifted, evidence)
            self.touched.add((identifier, written))
        if previous is not None:
            for written in curated_pointers(previous):
                if written not in curation:
                    self._drop(identifier, previous, written, _tokens(written))
                    self.touched.add((identifier, written))
        elif whole is not None:
            for written, inferred in inferences.items():
                if written not in curation:
                    self.tombstones[(identifier, written)] = Tombstone(
                        identifier, written, _copy(inferred), whole.version
                    )
        new["curation"] = curation
        self.removed.discard(identifier)
        self.written[identifier] = new
        if previous is None:
            self.touched.add((identifier, ""))

    def remove_descriptor(self, where: Where, identifier: str) -> None:
        at = (*where, "descriptor")
        descriptor = self.existing(identifier, at)
        kind = descriptor["kind"]
        fields = cast(Json, descriptor.get("fields", {}))
        if kind == "dataset":
            raise _refused(RefusalCode.INVALID_VALUE, at, "A release has its dataset descriptor")
        if kind == "table" or (kind == "column" and "derived" not in fields):
            raise _refused(
                RefusalCode.COLUMNS_CHANGED,
                at,
                "Tables and source columns are the source's: removing one is a re-import",
            )
        inferences: Json = {
            written: _copy(tombstone.inferred)
            for (owner, written), tombstone in self.tombstones.items()
            if owner == identifier
        }
        for key in [key for key in self.tombstones if key[0] == identifier]:
            del self.tombstones[key]
        for written, entry in cast(dict[str, Json], descriptor.get("curation", {})).items():
            if "inferred" in entry:
                inferences[written] = _copy(entry["inferred"])
        if inferences:
            version = cast(int, descriptor["version"])
            self.tombstones[(identifier, "")] = Tombstone(
                identifier, "", dict(sorted(inferences.items())), version
            )
        del self.written[identifier]
        self.removed.add(identifier)
        self.touched.add((identifier, ""))

    def accept(self, where: Where, proposal: StoredProposal | None, number: int) -> None:
        at = (*where, "proposal")
        if proposal is None:
            raise _refused(
                RefusalCode.UNKNOWN_PROPOSAL,
                at,
                f"No open proposal of the dataset has the id {number}, or the draft accepted it",
            )
        if proposal.id in self.accepted:
            raise _refused(RefusalCode.CONFLICT, at, "The change accepts the proposal twice")
        evidence = f"Proposal {proposal.id}"
        self.apply_proposal(at, at, proposal, evidence)
        self.accepted.append(proposal.id)

    def apply_proposal(
        self, where: Where, named: Where, proposal: StoredProposal, evidence: str | None
    ) -> None:
        """Apply a proposal; ``named`` is where a descriptor it names but the release lacks is
        refused."""
        identifier, written = proposal.descriptor, proposal.pointer
        if written == "":
            if proposal.remove:
                self.remove_descriptor(where, identifier)
            else:
                given = proposal.value
                if isinstance(given, dict) and given.get("id") != identifier:
                    raise _refused(
                        RefusalCode.INVALID_VALUE,
                        where,
                        "A proposed descriptor has the id the proposal names",
                    )
                self.put(where, given, evidence)
            return
        if self.get(identifier) is None:
            raise _refused(
                RefusalCode.UNKNOWN_DESCRIPTOR,
                named,
                "The release has no descriptor ",
                data(identifier),
            )
        if proposal.remove:
            self.remove(where, identifier, written)
        else:
            self.set(where, identifier, written, proposal.value, evidence)

    # --- The result ----------------------------------------------------------------------------

    def finish(self, root: str) -> Applied:
        found: dict[str, Descriptor] = {
            identifier: descriptor
            for identifier, descriptor in self.given.items()
            if identifier not in self.removed and identifier not in self.written
        }
        refusals: list[Refusal] = []
        for identifier, written in sorted(self.written.items()):
            descriptor = self._validated(identifier, written, root, refusals)
            if descriptor is not None:
                found[identifier] = descriptor
        if refusals:
            raise EditRefused(refusals)
        return Applied(
            tuple(found[identifier] for identifier in sorted(found)),
            ordered(self.tombstones.values()),
            tuple(sorted(self.touched)),
            tuple(self.accepted),
        )

    def _validated(
        self, identifier: str, written: Json, root: str, refusals: list[Refusal]
    ) -> Descriptor | None:
        prefix = f"/{root}/{escape_token(identifier)}"
        try:
            source = canonical(written)
        except JsonError as error:
            refusals.append(
                Refusal(
                    code=RefusalCode(error.code),
                    path=prefix + (error.pointer or ""),
                    message=[text(error.message)],
                )
            )
            return None
        loaded = load_descriptor(source)
        for refusal in loaded.refusals:
            path = prefix + (refusal.path or "") if refusal.path is not None else prefix
            refusals.append(refusal.model_copy(update={"path": path}))
        return loaded.descriptor


def _shaped(given: Json, at: Where) -> None:
    """Refuse a whole descriptor whose ``fields``, ``extensions`` or extension objects are not
    JSON objects, before its curated fields are listed."""
    fields = given.get("fields", {})
    if not isinstance(fields, dict):
        raise _refused(
            RefusalCode.WRONG_TYPE, (*at, "fields"), "A descriptor's fields are an object"
        )
    extensions = given.get("extensions", {})
    if not isinstance(extensions, dict):
        raise _refused(
            RefusalCode.WRONG_TYPE, (*at, "extensions"), "A descriptor's extensions are an object"
        )
    for pack, members in extensions.items():
        if not isinstance(members, dict):
            raise _refused(
                RefusalCode.WRONG_TYPE,
                (*at, "extensions", pack),
                "An extension is an object, by pack id",
            )


def _same(left: JsonValue, right: JsonValue) -> bool:
    try:
        return canonical(left) == canonical(right)
    except JsonError:
        return False


def _content(descriptor: Descriptor) -> bytes:
    dumped = cast(Json, descriptor.model_dump(mode="json"))
    del dumped["version"], dumped["curation"]
    return canonical(dumped)


def holds(descriptors: Mapping[str, Descriptor], proposal: StoredProposal) -> bool:
    """Whether ``descriptors``, by id, hold what ``proposal`` proposes (D248): its value for the
    field, the descriptor as ``put`` would write it over the one there (replayed as ``proposed``
    by its proposer, as ``propose`` applies it, so that the comparison decides), or the removal of
    either. Accepting a proposal and then editing the draft away from it leaves it unaccepted."""
    found = descriptors.get(proposal.descriptor)
    if proposal.pointer == "":
        if proposal.remove or found is None:
            return proposal.remove and found is None
        draft = _Draft([found], (), status="proposed", by=proposal.proposer, at=proposal.at)
        try:
            draft.put((), proposal.value, None)
            written = draft.finish("draft").descriptors
        except EditRefused:
            return False
        return len(written) == 1 and _content(written[0]) == _content(found)
    if found is None:
        return proposal.remove
    try:
        tokens = _curated((), proposal.pointer)
    except EditRefused:
        return False
    has, value = _value(cast(Json, found.model_dump(mode="json")), tokens)
    if proposal.remove:
        return not has
    return has and _same(value, proposal.value)


def apply(
    descriptors: Sequence[Descriptor],
    tombstones: Sequence[Tombstone],
    edits: Sequence[Edit],
    *,
    by: str,
    at: str,
    proposals: Mapping[int, StoredProposal],
    root: str = "draft",
) -> Applied:
    """The draft after ``edits`` by the operator ``by`` at ``at``; ``proposals`` are the open
    proposals the draft has not accepted, by id. Raises ``EditRefused``."""
    draft = _Draft(descriptors, tombstones, status="asserted", by=by, at=at)
    handlers: dict[type, Callable[[Where, Edit], None]] = {
        SetField: lambda where, edit: draft.set(
            where,
            cast(SetField, edit).descriptor,
            cast(SetField, edit).pointer,
            cast(SetField, edit).value,
            cast(SetField, edit).evidence,
        ),
        RemoveField: lambda where, edit: draft.remove(
            where, cast(RemoveField, edit).descriptor, cast(RemoveField, edit).pointer
        ),
        Confirm: lambda where, edit: draft.confirm(
            where,
            cast(Confirm, edit).descriptor,
            cast(Confirm, edit).pointers,
            cast(Confirm, edit).evidence,
        ),
        PutDescriptor: lambda where, edit: draft.put(
            where, cast(PutDescriptor, edit).descriptor, cast(PutDescriptor, edit).evidence
        ),
        RemoveDescriptor: lambda where, edit: draft.remove_descriptor(
            where, cast(RemoveDescriptor, edit).descriptor
        ),
        Accept: lambda where, edit: draft.accept(
            where,
            proposals.get(cast(Accept, edit).proposal),
            cast(Accept, edit).proposal,
        ),
    }
    for index, edit in enumerate(edits):
        handlers[type(edit)](("edits", index), edit)
    return draft.finish(root)


def propose(
    descriptors: Sequence[Descriptor],
    tombstones: Sequence[Tombstone],
    proposal: StoredProposal,
    *,
    root: str = "descriptors",
) -> Applied:
    """The release after ``proposal``, stamped ``proposed`` by its proposer: a proposal is
    recorded only if it would make valid descriptors (D248). Raises ``EditRefused``."""
    draft = _Draft(descriptors, tombstones, status="proposed", by=proposal.proposer, at=proposal.at)
    draft.apply_proposal((), ("descriptor",), proposal, proposal.evidence)
    return draft.finish(root)


__all__ = [
    "Applied",
    "EditRefused",
    "apply",
    "curated_pointers",
    "holds",
    "propose",
    "remove_field",
]

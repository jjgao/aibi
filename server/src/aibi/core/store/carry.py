"""Carrying curation forward on re-import (SPEC §12.3, D239, D240, D241).

``carry_forward`` takes the latest release's descriptors and tombstones and what the importer
proposes now, and gives the descriptors to import, by descriptor id:

- tables and source columns exist exactly when the new import has them; one that is gone is
  removed, and so is every other descriptor the base had that names something no longer there,
  down the chain (a relationship whose column is gone, then its coverage; a derived column whose
  input is gone);
- a descriptor the importer no longer proposes, and whose fields it had inferred, is removed; one
  an operator added (no field of it inferred), a derived column among them, is carried as it is;
- a descriptor new to the release is added as the importer proposes it, unless a tombstone of it
  holds the same inferences, when it stays removed; if they differ, the tombstone is consumed and
  the whole new descriptor returns, unless it names a descriptor whose removal stands, when it is
  dropped, as one without a tombstone that names such a descriptor is, whatever the statuses of
  its fields;
- for a descriptor in both, the dataset's included, each field is compared by its inferences, as
  RFC 8785 bytes: the previous one is the base entry's ``inferred`` (present when the member is
  set, ``null`` included) or a field tombstone's, and the new one the importer's, or *absent*
  when it no longer sets the field. Equal, the base's value and entry are carried verbatim, and a
  tombstoned field stays absent. Different, the field takes the importer's new proposal and entry,
  or is removed when the new inference is absent. A field with no previous inference that has a
  value is an operator's, and is carried; the importer's new inference is not written into it, so
  that an unchanged re-import changes nothing (D241). A field with no value and no previous
  inference takes the new proposal. The dataset's ``packs`` is the base's, value and entry: which
  packs a dataset uses is the operator's, not the source's. ``provenance``, which has no curation
  entry (§5.1), is the new import's when it has one, else the base's, so that an operator's
  ``put`` of it survives an importer that sets none;
- a field tombstone of a descriptor new to the release (one the gate dropped from the base, say)
  keeps the field out of it while the importer's inference is the same, as for a descriptor in
  both, unless the descriptor does not hold without the field, when it returns.

A tombstone is kept only while the importer's inference equals it: one that differs, or is absent
(the importer no longer infers the field, or no longer proposes the descriptor), consumes it
(D240), whether the descriptor enters the release or not (a descriptor dropped, or one returning
that a check drops, keeps no tombstone of other inferences). So every tombstone a release keeps
holds the latest import's inference, which an edit that lifts it records in its entry (D245); a
removal that survived an import without the inference would record a stale one there, which the
next re-import would read as a change.

A carried value that no longer holds against the new descriptors (its model, or
``check_release``) is replaced by the new proposal when its status is ``proposed`` or
``imported_default``, since it is still a guess. A new proposal of the importer's (status
``proposed``) that does not hold against the carried curation is dropped, as at import: the field
alone when its descriptor holds without it, a descriptor new to the release included; else, in a
descriptor the base has, the field goes back to the base's value and entry; else the whole
descriptor, when it is new to the release or a guess of the base's that holds no asserted field,
with the descriptors that name it, unless one of those holds an asserted field. Anything else
refuses the re-import, naming the field (``CarryRefused``, paths ``/descriptors/<id>/…``): a
failing field of a table or source column new to the release that it cannot hold without, or that
the importer did not merely propose, say. The validation gate then runs on the result, in import
mode (D241).

Every change is reported (``Change``): a field that took a new proposal (``proposed``), was
removed (``removed``) or returned from a tombstone (``returned``); a new proposal dropped
(``dropped``), whether the field went or went back to the base's; and a descriptor added
(``added``), removed (``removed``, the pointer ``""``), returned, gone (``gone``) or dropped.
Versions are the caller's (``writes.versions``, D243).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import JsonValue, TypeAdapter

from aibi.core.schema.descriptors import RELEASE_KINDS, Descriptor
from aibi.core.schema.jsonio import canonical, escape_token
from aibi.core.schema.loading import load_descriptor
from aibi.core.schema.refusals import Refusal, RefusalCode, finish_refusals
from aibi.core.schema.release import check_release
from aibi.core.store.edits import curated_pointers, remove_field
from aibi.core.store.tombstones import Tombstone, ordered

Json = dict[str, JsonValue]
Happened = Literal["proposed", "removed", "returned", "added", "gone", "dropped"]
_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)


@dataclass(frozen=True)
class Change:
    descriptor: str
    pointer: str
    """``""`` for a whole descriptor."""
    happened: Happened


@dataclass(frozen=True)
class Carried:
    descriptors: tuple[Descriptor, ...]
    """By id; their versions are the importer's until the caller sets them."""
    tombstones: tuple[Tombstone, ...]
    changes: tuple[Change, ...]


class CarryRefused(Exception):  # noqa: N818 - the spec's word
    def __init__(self, refusals: Sequence[Refusal]) -> None:
        super().__init__("; ".join(f"{refusal.code} at {refusal.path}" for refusal in refusals))
        self.refusals = tuple(finish_refusals(list(refusals)))


def _tokens(written: str) -> list[str]:
    return [token.replace("~1", "/").replace("~0", "~") for token in written.split("/")[1:]]


def _field(descriptor: Json, written: str) -> tuple[bool, JsonValue]:
    tokens = _tokens(written)
    node: JsonValue = descriptor
    for token in tokens:
        if not isinstance(node, dict) or token not in node:
            return (False, None)
        node = node[token]
    return (True, node)


def _put(descriptor: Json, written: str, value: JsonValue) -> None:
    tokens = _tokens(written)
    node = descriptor
    for token in tokens[:-1]:
        node = cast(Json, node.setdefault(token, {}))
    node[tokens[-1]] = value


def _same(left: JsonValue, right: JsonValue) -> bool:
    return canonical(left) == canonical(right)


def _source(descriptor: Json) -> bool:
    """Whether a written descriptor is the source's: the dataset, a table or a source column."""
    kind = descriptor.get("kind")
    fields = cast(Json, descriptor.get("fields", {}))
    return kind in ("dataset", "table") or (kind == "column" and "derived" not in fields)


def _curation(descriptor: Json) -> dict[str, Json]:
    return cast(dict[str, Json], descriptor.get("curation", {}))


def _inferences(descriptor: Json) -> Json:
    """A descriptor's inferences, by pointer, as a descriptor tombstone holds them."""
    return {
        written: entry["inferred"]
        for written, entry in sorted(_curation(descriptor).items())
        if "inferred" in entry
    }


def with_inferences(descriptors: Sequence[Descriptor]) -> list[Descriptor]:
    """The importer's descriptors, each entry by an importer that has no ``inferred`` given its
    field's value as one: what an importer proposes is its inference (§5.1, D227), so that a
    pack's importer is carried forward as the core's is."""
    found: list[Descriptor] = []
    for descriptor in descriptors:
        missing = [
            written
            for written, entry in descriptor.curation.items()
            if entry.by.startswith("importer:") and "inferred" not in entry.model_fields_set
        ]
        if descriptor.kind not in RELEASE_KINDS or not missing:
            found.append(descriptor)
            continue
        dumped = cast(Json, descriptor.model_dump(mode="json"))
        for written in missing:
            _curation(dumped)[written]["inferred"] = _field(dumped, written)[1]
        found.append(_ADAPTER.validate_python(dumped))
    return found


class _Carrying:
    def __init__(
        self,
        base: Sequence[Descriptor],
        tombstones: Sequence[Tombstone],
        new: Sequence[Descriptor],
    ) -> None:
        self.base = {descriptor.id: descriptor for descriptor in base}
        self.new = {descriptor.id: descriptor for descriptor in new}
        self.base_dumps: dict[str, Json] = {}
        self.new_dumps: dict[str, Json] = {}
        self.tombstones = {tombstone.key: tombstone for tombstone in tombstones}
        self.out: dict[str, Json] = {}
        self.carried: set[tuple[str, str]] = set()
        """Fields carried from the base, which a failing check may replace."""
        self.fresh: set[tuple[str, str]] = set()
        """Fields that took the importer's new proposal, which a failing check may drop."""
        self.reverted: set[tuple[str, str]] = set()
        """Fields a failing check sent back to the base's value and entry, as guesses of the
        base's that a failing check may drop."""
        self.added: set[str] = set()
        """Descriptors new to the release, as the importer proposes them."""
        self.dropped: set[str] = set()
        """Descriptors a failing check dropped, and new ones naming a descriptor whose removal
        stands."""
        self.lifted: dict[str, list[Tombstone]] = {}
        """Field tombstones of the importer's inference that a descriptor new to the release
        consumed only because it does not hold without their fields, which a drop puts back."""
        self.changes: dict[tuple[str, str], Happened] = {}
        self.standing = self._standing()
        """Descriptors the importer proposes whose removal stands: a tombstone of the same
        inferences."""

    def _standing(self) -> set[str]:
        return {
            tombstone.descriptor
            for tombstone in self.tombstones.values()
            if tombstone.pointer == ""
            and tombstone.descriptor in self.new
            and tombstone.descriptor not in self.base
            and _same(tombstone.inferred, _inferences(self.dump(tombstone.descriptor, new=True)))
        }

    def dump(self, identifier: str, *, new: bool) -> Json:
        cache, source = (self.new_dumps, self.new) if new else (self.base_dumps, self.base)
        if identifier not in cache:
            cache[identifier] = cast(Json, source[identifier].model_dump(mode="json"))
        return cache[identifier]

    def run(self) -> None:
        for identifier in sorted(self.base.keys() | self.new.keys()):
            before, after = self.base.get(identifier), self.new.get(identifier)
            if identifier == "dataset" and before is not None and after is None:
                self.out[identifier] = self.dump(identifier, new=False)
            elif after is None:
                assert before is not None
                if _source(self.dump(identifier, new=False)):
                    self.changes[(identifier, "")] = "gone"
                elif any("inferred" in e.model_fields_set for e in before.curation.values()):
                    self.changes[(identifier, "")] = "removed"
                else:
                    self.out[identifier] = self.dump(identifier, new=False)
            elif before is None:
                self.arrive(identifier)
            else:
                self.merge(identifier)

    def arrive(self, identifier: str) -> None:
        new = self.dump(identifier, new=True)
        whole = self.tombstones.get((identifier, ""))
        if identifier in self.standing:
            return
        if whole is not None:
            del self.tombstones[whole.key]
        if _names(new.get("fields"), self.standing):
            self.dropped.add(identifier)
            self.changes[(identifier, "")] = "dropped"
            return
        self.out[identifier] = self.without_removed(identifier, new)
        self.added.add(identifier)
        if whole is not None:
            self.changes[(identifier, "")] = "returned"
        else:
            self.changes[(identifier, "")] = "added"

    def without_removed(self, identifier: str, new: Json) -> Json:
        """A descriptor new to the release without the fields whose tombstones hold the importer's
        inference, and the other field tombstones consumed; all of them consumed when it does not
        hold without those fields."""
        tombstones = [
            tombstone
            for (owner, written), tombstone in sorted(self.tombstones.items())
            if owner == identifier and written
        ]
        if not tombstones:
            return new
        found = cast(Json, _copy(new))
        standing: list[Tombstone] = []
        for tombstone in tombstones:
            has, proposal = _field(found, tombstone.pointer)
            entry = _curation(found).get(tombstone.pointer, {})
            if has and _same(tombstone.inferred, entry.get("inferred", proposal)):
                remove_field(found, tombstone.pointer)
                del _curation(found)[tombstone.pointer]
                standing.append(tombstone)
        if standing and load_descriptor(canonical(found)).refusals:
            self.lifted[identifier] = standing
            found, standing = new, []
        for tombstone in tombstones:
            if tombstone in standing:
                continue
            del self.tombstones[tombstone.key]
            if _field(found, tombstone.pointer)[0]:
                self.changes[tombstone.key] = "returned"
        return found

    def merge(self, identifier: str) -> None:
        before, after = self.dump(identifier, new=False), self.dump(identifier, new=True)
        merged: Json = {
            key: value
            for key, value in before.items()
            if key not in ("label", "definition", "fields", "extensions", "curation", "provenance")
        }
        for provenanced in (after, before):
            if "provenance" in provenanced:
                merged["provenance"] = provenanced["provenance"]
                break
        merged["fields"] = {}
        entries: dict[str, JsonValue] = {}
        tombstoned = {written for (owner, written) in self.tombstones if owner == identifier}
        pointers = set(curated_pointers(before)) | set(curated_pointers(after))
        for written in sorted(pointers | (tombstoned - {""})):
            chosen = self.field(identifier, written, before, after)
            if chosen is None:
                continue
            source, value, entry = chosen
            _put(merged, written, value)
            entries[written] = entry
            if source == "base":
                self.carried.add((identifier, written))
            elif cast(Json, entry).get("status") == "proposed":
                self.fresh.add((identifier, written))
        if not merged.get("extensions"):
            merged.pop("extensions", None)
        merged["curation"] = entries
        self.out[identifier] = merged

    def field(
        self, identifier: str, written: str, before: Json, after: Json
    ) -> tuple[str, JsonValue, JsonValue] | None:
        """Where the field comes from (``base`` or ``new``), its value and its entry; ``None``
        when it has no value."""
        had, value = _field(before, written)
        entry = _curation(before).get(written)
        has, proposal = _field(after, written)
        proposed = _curation(after).get(written)
        tombstone = self.tombstones.get((identifier, written))
        if identifier == "dataset" and written == "/fields/packs":
            return ("base", value, cast(JsonValue, entry)) if had else None
        previous: tuple[JsonValue, bool] | None = None
        if had and entry is not None and "inferred" in entry:
            previous = (entry["inferred"], False)
        elif not had and tombstone is not None:
            previous = (tombstone.inferred, True)
        inferred: tuple[JsonValue] | None = None
        if has:
            inferred = (proposed["inferred"] if proposed and "inferred" in proposed else proposal,)
        if previous is None:
            if had:
                return ("base", value, cast(JsonValue, entry))
            if has:
                self.changes[(identifier, written)] = "proposed"
                return ("new", proposal, cast(JsonValue, proposed))
            return None
        inference, from_tombstone = previous
        if inferred is not None and _same(inference, inferred[0]):
            return None if from_tombstone else ("base", value, cast(JsonValue, entry))
        if from_tombstone:
            del self.tombstones[(identifier, written)]
            if not has:
                return None
        if has:
            self.changes[(identifier, written)] = "returned" if from_tombstone else "proposed"
            return ("new", proposal, cast(JsonValue, proposed))
        self.changes[(identifier, written)] = "removed"
        return None

    # --- Checks on what was carried --------------------------------------------------------------

    def inferred_still(self, tombstone: Tombstone) -> bool:
        """Whether the importer infers what a tombstone of a descriptor it proposes holds, the
        descriptor staying out of the release (its removal standing, or dropped)."""
        new = self.dump(tombstone.descriptor, new=True)
        if tombstone.pointer == "":
            return _same(tombstone.inferred, _inferences(new))
        has, proposal = _field(new, tombstone.pointer)
        entry = _curation(new).get(tombstone.pointer, {})
        return has and _same(tombstone.inferred, entry.get("inferred", proposal))

    def replace(self, identifier: str, written: str) -> bool:
        """Replace a carried guess (``proposed`` or ``imported_default``) with the new proposal;
        whether it was one."""
        if (identifier, written) not in self.carried or identifier not in self.new:
            return False
        merged = self.out[identifier]
        entry = _curation(merged).get(written)
        if entry is None or entry.get("status") not in ("proposed", "imported_default"):
            return False
        after = self.dump(identifier, new=True)
        has, proposal = _field(after, written)
        self.carried.discard((identifier, written))
        remove_field(merged, written)
        del _curation(merged)[written]
        if has:
            _put(merged, written, proposal)
            _curation(merged)[written] = _curation(after)[written]
            self.changes[(identifier, written)] = "proposed"
            if _curation(after)[written].get("status") == "proposed":
                self.fresh.add((identifier, written))
        else:
            self.changes[(identifier, written)] = "removed"
        return True

    def drop(self, identifier: str, written: str | None) -> bool:
        """Drop a new proposal of the importer's that fails a check (D241); whether it was one.

        The proposal is a field that took the importer's proposal (``proposed``), a guess of the
        base's that such a field went back to, or a whole descriptor new to the release whose
        failing field is proposed. A field is dropped alone when its descriptor holds without it,
        a new descriptor's included; otherwise a field that took the importer's proposal goes back
        to the base's value and entry, when the base has one; otherwise the whole descriptor is
        dropped, unless it is the source's or a descriptor of the base's that holds a value someone
        asserted."""
        found = self.out.get(identifier)
        if found is None or written is None:
            return False
        key = (identifier, written)
        if _curation(found).get(written, {}).get("status") != "proposed":
            return False
        added = identifier in self.added
        if not added and key not in self.fresh and key not in self.reverted:
            return False
        without = cast(Json, _copy(found))
        remove_field(without, written)
        del _curation(without)[written]
        if not load_descriptor(canonical(without)).refusals:
            self.fresh.discard(key)
            self.reverted.discard(key)
            self.out[identifier] = without
            self.changes[key] = "dropped"
            return True
        if not added and key in self.fresh and self.revert(identifier, written):
            return True
        asserted = any(entry.get("status") == "asserted" for entry in _curation(found).values())
        if _source(found) or (asserted and not added):
            return False
        self.discard(identifier)
        return True

    def revert(self, identifier: str, written: str) -> bool:
        """Send a field that took the importer's failing proposal back to the base's value and
        entry; whether the base has one."""
        had, value = _field(self.dump(identifier, new=False), written)
        if not had:
            return False
        merged = self.out[identifier]
        _put(merged, written, value)
        _curation(merged)[written] = _curation(self.dump(identifier, new=False))[written]
        self.fresh.discard((identifier, written))
        self.reverted.add((identifier, written))
        self.changes[(identifier, written)] = "dropped"
        return True

    def discard(self, identifier: str) -> None:
        """Drop a whole descriptor, which is then reported as that alone. A descriptor tombstone
        it returned from is not put back, since its inferences differ from the import's (D240);
        field tombstones of the import's inference that it consumed only to hold are."""
        del self.out[identifier]
        for tombstone in self.lifted.pop(identifier, ()):
            self.tombstones[tombstone.key] = tombstone
        self.dropped.add(identifier)
        for key in [key for key in self.changes if key[0] == identifier]:
            del self.changes[key]
        self.changes[(identifier, "")] = "dropped"

    def gone(self, identifier: str) -> bool:
        """Remove a descriptor that names one a failing check dropped, unless it holds an asserted
        field, or a carried descriptor the new import does not have that names something gone;
        whether it was one."""
        found = self.out.get(identifier)
        if found is None or _source(found):
            return False
        if _names(found.get("fields"), self.dropped):
            if any(entry.get("status") == "asserted" for entry in _curation(found).values()):
                return False
            self.discard(identifier)
            return True
        if identifier in self.new:
            return False
        del self.out[identifier]
        self.changes[(identifier, "")] = "gone"
        return True

    def descriptors(self) -> tuple[list[Descriptor], list[Refusal]]:
        found: list[Descriptor] = []
        refusals: list[Refusal] = []
        for identifier, written in sorted(self.out.items()):
            known = self._known(identifier, written)
            if known is not None:
                found.append(known)
                continue
            loaded = load_descriptor(canonical(written))
            prefix = f"/{escape_token(identifier)}"
            refusals.extend(
                refusal.model_copy(update={"path": prefix + (refusal.path or "")})
                for refusal in loaded.refusals
            )
            if loaded.descriptor is not None:
                found.append(loaded.descriptor)
        return found, refusals

    def _known(self, identifier: str, written: Json) -> Descriptor | None:
        """The base's or the importer's descriptor, when ``written`` is it."""
        for dumps, source in ((self.base_dumps, self.base), (self.new_dumps, self.new)):
            if identifier in source:
                dumped = dumps.get(identifier)
                if dumped is written or (
                    dumped is not None and canonical(dumped) == canonical(written)
                ):
                    return source[identifier]
        return None

    def settle(self) -> list[Descriptor]:
        """The carried descriptors, once every carried guess that fails a check is replaced and
        every descriptor that names something gone is removed; raises ``CarryRefused``."""
        while True:
            found, refusals = self.descriptors()
            ids: list[str] | None = None
            if not refusals:
                ids = [descriptor.id for descriptor in found]
                refusals = check_release(found)
                if not refusals:
                    return found
            resolved = [self.resolve(refusal, by_position=ids) for refusal in refusals]
            if not any(resolved):
                shown = refusals if ids is None else [_by_id(refusal, ids) for refusal in refusals]
                raise CarryRefused([_rooted(refusal) for refusal in shown])

    def resolve(self, refusal: Refusal, *, by_position: Sequence[str] | None) -> bool:
        tokens = _tokens(refusal.path or "")
        if not tokens:
            return False
        head = tokens[0]
        if by_position is not None:
            if not head.isdigit() or int(head) >= len(by_position):
                return False
            identifier = by_position[int(head)]
        else:
            identifier = head
        rest = tokens[1:]
        written = _pointer_of(rest)
        if written is not None and self.replace(identifier, written):
            return True
        if refusal.code == RefusalCode.UNKNOWN_DESCRIPTOR and self.gone(identifier):
            return True
        return self.drop(identifier, written)

    def result(self, descriptors: Sequence[Descriptor]) -> Carried:
        kept = [
            tombstone
            for tombstone in self.tombstones.values()
            if tombstone.descriptor in self.new
            and (tombstone.pointer != "" or tombstone.descriptor not in self.out)
            and (tombstone.descriptor in self.out or self.inferred_still(tombstone))
        ]
        changes = tuple(
            Change(identifier, written, happened)
            for (identifier, written), happened in sorted(self.changes.items())
        )
        return Carried(tuple(descriptors), ordered(kept), changes)


def _copy(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: _copy(member) for key, member in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def _names(value: JsonValue, identifiers: set[str]) -> bool:
    """Whether a value names one of ``identifiers``, anywhere in it."""
    if isinstance(value, str):
        return value in identifiers
    if isinstance(value, list):
        return any(_names(item, identifiers) for item in value)
    if isinstance(value, dict):
        return any(_names(item, identifiers) for item in value.values())
    return False


def _pointer_of(tokens: Sequence[str]) -> str | None:
    """The curated field a path inside a descriptor is in, if any."""
    if not tokens:
        return None
    if tokens[0] in ("label", "definition"):
        return "/" + tokens[0]
    if tokens[0] == "fields" and len(tokens) > 1:
        return "/fields/" + escape_token(tokens[1])
    if tokens[0] == "extensions" and len(tokens) > 2:
        return f"/extensions/{escape_token(tokens[1])}/{escape_token(tokens[2])}"
    if tokens[0] == "curation" and len(tokens) > 1:
        return tokens[1]
    return None


def _by_id(refusal: Refusal, ids: Sequence[str]) -> Refusal:
    tokens = (refusal.path or "").split("/")
    if len(tokens) > 1 and tokens[1].isdigit() and int(tokens[1]) < len(ids):
        tokens[1] = escape_token(ids[int(tokens[1])])
        return refusal.model_copy(update={"path": "/".join(tokens)})
    return refusal


def _rooted(refusal: Refusal) -> Refusal:
    if refusal.path is None:
        return refusal
    return refusal.model_copy(update={"path": "/descriptors" + refusal.path})


def carry_forward(
    base: Sequence[Descriptor], tombstones: Sequence[Tombstone], new: Sequence[Descriptor]
) -> Carried:
    """The descriptors a re-import writes, from the latest release's ``base`` and ``tombstones``
    and the importer's ``new`` descriptors (D239). Raises ``CarryRefused``."""
    carrying = _Carrying(base, tombstones, new)
    carrying.run()
    return carrying.result(carrying.settle())


__all__ = [
    "Carried",
    "CarryRefused",
    "Change",
    "Happened",
    "carry_forward",
    "with_inferences",
]

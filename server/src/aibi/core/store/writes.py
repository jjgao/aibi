"""Checks on every descriptor write, and the versions the server sets (SPEC §5.1, §10.1, D243,
D247).

A descriptor write is an import, a re-import, a change to a draft or a proposal. Its descriptors
are checked (``check_writes``) against the registered packs, beyond what their models and
``check_release`` check:

- every pack the dataset lists (``packs``) is registered;
- each extension object ``extensions[<pack>]`` validates against that pack's JSON Schema for the
  descriptor's kind (``INVALID_EXTENSION`` at the member that fails, by the keyword that fails it,
  never by the value, or as one its schema cannot evaluate), within a step budget linear in the
  object and capped whatever the schema (``LIMIT_EXCEEDED``, ``extension_steps``): at
  ``STEPS_MAX`` for a proposal, and at ``WRITE_STEPS_MAX``, which caps no descriptor within §14's
  limits, for an operator's or an importer's write; a pack with no schema for the kind
  refuses every extension object of it, and an extension of a pack neither registered nor listed
  is refused;
- each ontology code of a system some registered pack validates (a dataset's ``data_use``, a
  column's ``concepts`` and its permissible values' ``concepts``) is one the validator accepts
  (``INVALID_VALUE``), the validator called through its handle's guard and failing closed
  (``PACK_FAILED`` at the code, D403); a system no pack registered is not checked.

A pack's validators run on their packs' views of a release in one operation (``validated``), each
through its handle's guard: what a validator raises refuses the release (``PACK_FAILED``), so that
no release passes a validator that did not run (D403).

Paths point into the descriptors given, by position, as ``check_release``'s do; ``by_id`` rewrites
them by descriptor id, as a draft change reports them (D246). An import, a re-import and a session's
publish check every descriptor against the registry they run under. A change or a proposal checks
only the descriptors it adds or changes (``changed``), for speed: one large extension value then
costs its own writes, not every later one. The extensions of the others are still checked against
the dataset's ``packs``, which needs no schema; what the registry decides about them (their
schemas, the packs listed, ontology codes) is checked again when the session publishes, since a
pack removed or upgraded since they were written would otherwise let a release hold descriptors the
running registry refuses (D247).

``versions`` sets each descriptor's ``version`` against the release a write starts from (D243):
its version there when nothing but ``version`` differs, curation included, and that version plus
one otherwise; a descriptor new to the release has 1, and one returning from a tombstone that
tombstone's version plus one. Versions are counted per release, not per edit, so a draft edited
back to its base gives back the base's bytes.
"""

from collections.abc import Container, Iterable, Sequence
from typing import Literal

from pydantic import JsonValue, TypeAdapter, ValidationError

from aibi.core.engine.resolve import LabelledView, Operation
from aibi.core.schema.copiers import is_true, pack_refusals
from aibi.core.schema.descriptors import (
    RELEASE_KINDS,
    By,
    ColumnDescriptor,
    DatasetDescriptor,
    Descriptor,
)
from aibi.core.schema.guards import Hook, PackFailed
from aibi.core.schema.jsonio import canonical, escape_token, pointer
from aibi.core.schema.jsonschemas import (
    OUT_OF_STEPS,
    STEPS_BASE,
    STEPS_MAX,
    STEPS_PER_VALUE,
    UNEVALUABLE,
    WRITE_STEPS_MAX,
    Checker,
    steps,
)
from aibi.core.schema.limits import EXTENSION_STEPS
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.pack_api import JsonSchema, OntologyValidator, PackRegistry, Validator
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode, finish_refusals
from aibi.core.store.tombstones import Tombstone

_BY: TypeAdapter[str] = TypeAdapter(By)


def attributed(by: str, kinds: Iterable[str]) -> bool:
    """Whether ``by`` is a valid attribution (§5.1) of one of ``kinds`` (``operator``, ``model``,
    ``agent``, ``importer``). The server sets it (§11.1); this checks what the caller gives."""
    try:
        _BY.validate_python(by)
    except ValidationError:
        return False
    return by.partition(":")[0] in set(kinds)


def packs_of(descriptors: Iterable[Descriptor]) -> list[str]:
    """The packs the dataset descriptor lists."""
    for descriptor in descriptors:
        if isinstance(descriptor, DatasetDescriptor):
            return list(descriptor.fields.packs or ())
    return []


def validators(registry: PackRegistry | None, packs: Iterable[str]) -> list[Hook[Validator]]:
    """The handles on the validators of the registered packs among ``packs``; ``check_writes``
    refuses the others."""
    if registry is None:
        return []
    return registry.validators([pack for pack in packs if pack in registry.ids])


def failed(pack: str, what: str, path: Sequence[str | int] | None = None) -> Refusal:
    """The refusal of a pack's hook that failed (D403): ``PACK_FAILED``, in the core's words."""
    return Refusal(
        code=RefusalCode.PACK_FAILED,
        path=None if path is None else pointer(path),
        message=[text(f"The {what} of the pack "), data(pack), text(" failed")],
    )


def validated(
    validators: Sequence[Hook[Validator]],
    operation: Operation,
    dataset: str,
    manifest: str,
    label: int | Literal["draft"],
    descriptors: Sequence[Descriptor],
) -> list[Refusal]:
    """The refusals of each validator's ``validate_descriptors`` of a release, each given its
    pack's view of it in ``operation``; a validator that fails refuses the release
    (``PACK_FAILED``), so that a release never passes a validator that did not run (D403)."""
    found: list[Refusal] = []
    for validator in validators:
        view = operation.labelled(validator.pack, dataset, manifest, label, descriptors)
        found.extend(_validated(validator, view))
    return found


def _validated(validator: Hook[Validator], view: LabelledView) -> list[Refusal]:
    pack = validator.pack
    try:
        return list(
            validator.call(lambda h: pack_refusals(h.validate_descriptors, view, pack=pack))
        )
    except PackFailed:
        return [failed(pack, "validator")]


def _in_system(validate: Hook[OntologyValidator], code: str) -> bool | None:
    """Whether an ontology system's validator takes ``code``; ``None`` when it fails."""
    try:
        return validate.call(lambda h: is_true(h, code))
    except PackFailed:
        return None


def _refusal(
    code: RefusalCode,
    path: Sequence[str | int],
    *message: Segment | str,
    limit: Limit | None = None,
) -> Refusal:
    segments = [text(part) if isinstance(part, str) else part for part in message]
    return Refusal(code=code, path=pointer(path), message=segments, limit=limit)


def _out_of_steps(where: Sequence[str | int], budget: int, ceiling: int) -> Refusal:
    remedy = "a smaller extension object, or a pack schema that evaluates it in fewer steps"
    if ceiling < WRITE_STEPS_MAX:
        remedy += ", or an operator's curation session, which allows more"
    return _refusal(
        RefusalCode.LIMIT_EXCEEDED,
        where,
        f"Evaluating the extension against its pack's schema takes more than its {budget} steps "
        f"({STEPS_BASE} and {STEPS_PER_VALUE} per JSON value of the extension, at most "
        f"{ceiling}): {remedy}",
        limit=Limit(name=EXTENSION_STEPS, max=budget),
    )


def _references(descriptor: Descriptor) -> list[tuple[tuple[str | int, ...], str, str]]:
    """The ontology references of a descriptor: (path in it, system, code)."""
    found: list[tuple[tuple[str | int, ...], str, str]] = []
    if isinstance(descriptor, DatasetDescriptor):
        for index, reference in enumerate(descriptor.fields.data_use or ()):
            found.append((("fields", "data_use", index), reference.system, reference.code))
    elif isinstance(descriptor, ColumnDescriptor):
        fields = descriptor.fields
        for index, reference in enumerate(fields.concepts or ()):
            found.append((("fields", "concepts", index), reference.system, reference.code))
        values = fields.permissible_values.values if fields.permissible_values else []
        for at, value in enumerate(values):
            for index, reference in enumerate(value.concepts or ()):
                where = ("fields", "permissible_values", "values", at, "concepts", index)
                found.append((where, reference.system, reference.code))
    return found


def changed(before: Iterable[Descriptor], after: Iterable[Descriptor]) -> set[str]:
    """The ids of the descriptors of ``after`` that ``before`` lacks or holds otherwise, for
    ``check_writes``."""
    previous = {descriptor.id: descriptor for descriptor in before}
    return {descriptor.id for descriptor in after if previous.get(descriptor.id) != descriptor}


def check_writes(
    descriptors: Sequence[Descriptor],
    registry: PackRegistry | None,
    *,
    only: Container[str] | None = None,
    ceiling: int = STEPS_MAX,
) -> list[Refusal]:
    """The refusals of the pack checks on a descriptor write (D247), as ``finish_refusals``
    returns them: of the descriptors whose ids are in ``only``, or of every one, each extension
    object evaluated within ``steps(value, ceiling)`` steps; the extensions of the others are
    checked against the dataset's ``packs`` alone. An ontology system whose validator failed is
    not called again in this check: its later codes are refused ``PACK_FAILED`` without a call,
    so that one failure is logged once (D403)."""
    registered: set[str] = set(registry.ids) if registry is not None else set()
    found: list[Refusal] = []
    listed = packs_of(descriptors)
    for at, descriptor in enumerate(descriptors):
        if only is not None and descriptor.id not in only:
            continue
        if isinstance(descriptor, DatasetDescriptor):
            for index, pack in enumerate(listed):
                if pack not in registered:
                    found.append(
                        _refusal(
                            RefusalCode.INVALID_VALUE,
                            (at, "fields", "packs", index),
                            "The dataset lists a pack that is not registered: ",
                            data(pack),
                        )
                    )
    checkers: dict[tuple[str, str], Checker | None] = {}
    failed_systems: set[str] = set()
    for at, descriptor in enumerate(descriptors):
        checked = only is None or descriptor.id in only
        for pack, members in sorted(descriptor.extensions.items()):
            where = (at, "extensions", pack)
            if registry is None or pack not in registered:
                if pack not in listed:
                    found.append(
                        _refusal(
                            RefusalCode.INVALID_EXTENSION,
                            where,
                            "No registered pack has the extension's pack id: ",
                            data(pack),
                        )
                    )
                continue
            if not checked:
                continue
            key = (pack, descriptor.kind)
            if key not in checkers:
                schema: JsonSchema | None = registry.extension_schemas(descriptor.kind, [pack]).get(
                    pack
                )
                checkers[key] = None if schema is None else Checker(schema)
            checker = checkers[key]
            if checker is None:
                found.append(
                    _refusal(
                        RefusalCode.INVALID_EXTENSION,
                        where,
                        f"The pack has no extension schema for {descriptor.kind} descriptors",
                    )
                )
                continue
            value: JsonValue = dict(members)
            for failure in checker.failures(value, ceiling=ceiling):
                if failure.keyword == OUT_OF_STEPS:
                    found.append(_out_of_steps(where, steps(value, ceiling), ceiling))
                    continue
                message: tuple[Segment | str, ...] = (
                    ("The pack's schema cannot evaluate the extension",)
                    if failure.keyword == UNEVALUABLE
                    else (
                        "The extension does not match its pack's schema (",
                        data(failure.keyword),
                        ")",
                    )
                )
                found.append(
                    _refusal(RefusalCode.INVALID_EXTENSION, (*where, *failure.path), *message)
                )
        if registry is None or not checked:
            continue
        for path, system, code in _references(descriptor):
            validate = registry.ontology_validator(system)
            if validate is None:
                continue
            holds = None if system in failed_systems else _in_system(validate, code)
            if holds is None:
                failed_systems.add(system)
                found.append(failed(validate.pack, "ontology validator", (at, *path, "code")))
            elif not holds:
                found.append(
                    _refusal(
                        RefusalCode.INVALID_VALUE,
                        (at, *path, "code"),
                        "The ontology system ",
                        data(system),
                        " has no such code",
                    )
                )
    return finish_refusals(found)


def _unversioned(descriptor: Descriptor) -> bytes:
    dumped = descriptor.model_dump(mode="json")
    del dumped["version"]
    return canonical(dumped)


def versions(
    base: Sequence[Descriptor],
    descriptors: Sequence[Descriptor],
    tombstones: Iterable[Tombstone] = (),
) -> tuple[Descriptor, ...]:
    """``descriptors`` with the versions the server sets against the release ``base`` and its
    ``tombstones`` (D243)."""
    before = {descriptor.id: descriptor for descriptor in base}
    returning = {t.descriptor: t.version for t in tombstones if t.pointer == ""}
    found: list[Descriptor] = []
    for descriptor in descriptors:
        if descriptor.kind not in RELEASE_KINDS:
            found.append(descriptor)
            continue
        earlier = before.get(descriptor.id)
        if earlier is not None:
            same = _unversioned(earlier) == _unversioned(descriptor)
            version = earlier.version if same else int(earlier.version) + 1
        elif descriptor.id in returning:
            version = returning[descriptor.id] + 1
        else:
            version = 1
        if version != descriptor.version:
            descriptor = descriptor.model_copy(update={"version": version})
        found.append(descriptor)
    return tuple(found)


def by_id(
    refusals: Iterable[Refusal], descriptors: Sequence[Descriptor], root: str
) -> list[Refusal]:
    """Refusals whose paths point into ``descriptors`` by position, pointed at
    ``/<root>/<descriptor id>/…`` instead (D246)."""
    found: list[Refusal] = []
    for refusal in refusals:
        path = refusal.path
        tokens = path.split("/") if path else []
        if len(tokens) > 1 and tokens[1].isdigit() and int(tokens[1]) < len(descriptors):
            tokens[1] = escape_token(descriptors[int(tokens[1])].id)
            refusal = refusal.model_copy(update={"path": "/".join(["", root, *tokens[1:]])})
        found.append(refusal)
    return finish_refusals(found)


def rooted(refusals: Iterable[Refusal], root: str) -> list[Refusal]:
    """A pack validator's refusals, whose paths point into the release's descriptors by id
    (``/<descriptor id>/…``), pointed at ``/<root>/<descriptor id>/…`` (D246)."""
    return finish_refusals(
        [
            refusal
            if refusal.path is None
            else refusal.model_copy(update={"path": f"/{root}{refusal.path}"})
            for refusal in refusals
        ]
    )


__all__ = [
    "attributed",
    "by_id",
    "changed",
    "check_writes",
    "failed",
    "packs_of",
    "rooted",
    "validated",
    "validators",
    "versions",
]

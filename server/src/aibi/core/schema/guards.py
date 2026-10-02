"""The one guard around every call of a pack's code (SPEC §10.1, D285, D343, D385, D387, D388).

The registry hands out a ``Hook`` for each hook object a pack registered, never the object
itself, and the core reaches a hook's code only through ``Hook.call(run)``: ``run`` is given the
hook object and ends in one of the copiers of ``schema.copiers``, which calls the hook and builds
a copy of the core's of what it gave. Whatever ``run`` raises is the hook's failure, logged by
``pack_failed`` and raised as a new ``PackFailed`` with no context, but for ``PASSED``, the
process out of memory or asked to stop, which only these types themselves pass, compared by
identity (never a subclass, nor a type whose metaclass says it equals one), each raised again as
a new instance outside every ``except`` (a ``SystemExit`` with status 1), without the pack's
message, traceback or context. ``contain`` is the same rule for one item of what a hook gave,
which ``copiers.proposals`` alone uses, so that one proposal's failure skips that one alone.

A copy's limits are an ``allowance`` (or a set of them, ``allowances``) that the site makes
outside ``run`` and hands to the copier; only that allowance's own trip, by identity, is a limit
(``Tripped``), and any other limit error is the hook's failure. ``NotJson`` is a copy's own
finding that what a hook gave is not JSON, and ``Late`` an analysis that returned past its call's
deadline: markers the copiers return, which the site maps outside the guard.

None of this is a sandbox (D285): a pack that reaches the core's internals by introspection (a
``Hook``'s slots through ``object.__setattr__`` included), installs process-wide hooks, acts
through finalizers or runs native code is outside what is guarded.
"""

import builtins
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, cast

from aibi.core.schema.limits import RESULT_CHARACTERS, RESULT_VALUES, TEXT_CHARACTERS

if TYPE_CHECKING:
    from aibi.core.schema.refusals import Limit

PASSED: tuple[type[BaseException], ...] = (MemoryError, KeyboardInterrupt, SystemExit)

_logger = logging.getLogger(__name__)


def passed(error: BaseException) -> type[BaseException] | None:
    """The type of ``PASSED`` that ``error`` is exactly, by identity, if any; it runs no code of
    the error's (its type's ``__eq__`` and ``__hash__`` included)."""
    kind = type(error)
    return next((one for one in PASSED if kind is one), None)


_BUILT_IN: tuple[tuple[type, str], ...] = tuple(
    (found, name)
    for name, found in vars(builtins).items()
    if isinstance(found, type) and issubclass(found, BaseException)
)
"""Each built-in exception type with its name in ``builtins``: the one name a log may give a
pack's exception, since a pack names its own types, and sets ``__module__``, as it likes. It is
looked up by identity, which runs no code of the pack's: a dict would hash the pack's type, and
its metaclass may make that raise, or say something else (D385)."""


class Unfit(ValueError):  # noqa: N818 - what a hook gave is unfit, as the core says it
    """What a copier raises for what a hook gave that it does not take, which ``Hook.call``
    makes the hook's failure; the log says so rather than naming an exception. A copier raises
    it, or ``_Foreign``, for every refusal of its own: a value it does not take, a fixed
    allowance of its own passed, and a read-back of a core model that fails (D388)."""


class _Foreign(Unfit):
    """A value of a type the count walk does not take, which the walk itself raises."""


_UNFIT: tuple[type[BaseException], ...] = (Unfit, _Foreign)
"""The core's own family of what a copier does not take, recognised by identity (never by
subclass), so that a pack's subclass of one is logged as an exception of its own."""


def pack_failed(pack: str, stage: str, error: BaseException) -> None:
    """Log that a pack's code raised: the pack and the exception's type only, since its message
    may quote the leaf or the release (D285), and the type's name only for a built-in exception,
    since a pack names its own types as it likes (D343); a copier's own refusal (``_UNFIT``, by
    identity) is logged as what the core does not take."""
    kind = type(error)
    if any(kind is one for one in _UNFIT):
        _logger.warning("pack %s: its %s gave what the core does not take", pack, stage)
        return
    _logger.warning(
        "pack %s: its %s raised %s",
        pack,
        stage,
        next((name for found, name in _BUILT_IN if found is kind), "an exception of its own"),
    )


class PackFailed(Exception):  # noqa: N818 - "failed" is the refusal's word, PACK_FAILED
    """A hook that raised, or gave what its copier does not take: ``pack`` and ``stage`` are
    the core's own, and ``limit`` a limit the site's own allowance found passed, if any. It
    carries nothing of the pack's, and ``Hook.call`` raises it with no context."""

    def __init__(self, pack: str, stage: str, limit: "Limit | None" = None) -> None:
        super().__init__(f"the {stage} of pack {pack} failed")
        self.pack = pack
        self.stage = stage
        self.limit = limit


def _raise_passed(kind: type[BaseException]) -> NoReturn:
    raise SystemExit(1) if kind is SystemExit else kind()


def _contained[R](pack: str, stage: str, run: Callable[[], R]) -> tuple[bool, R | None]:
    """``run()`` under the pass rule: ``(True, what it returned)``, or ``(False, None)`` when it
    raised anything but a ``PASSED`` type itself, which is logged (``pack_failed``); a passed
    type is raised as a new instance outside the handler. ``Hook.call`` and ``contain`` share
    it."""
    passing: type[BaseException] | None = None
    try:
        return True, run()
    except BaseException as error:  # every exception of the pack's is contained (D388)
        passing = passed(error)
        if passing is None:
            pack_failed(pack, stage, error)
    if passing is not None:
        _raise_passed(passing)
    return False, None


class Hook[T]:
    """A handle on one hook object of a registered pack (D388): its pack's id, its stage, and
    the object, which only ``call`` (and, for the importer, ``enter``) hands on. A handle is
    immutable, equal only to itself, copied as itself and never pickled, and its ``repr`` reads
    only its own text. Its slots can still be reached through ``object.__setattr__``, which is
    introspection (D285)."""

    __slots__ = ("_hook_object", "_hook_pack", "_hook_stage")

    _hook_object: T
    _hook_pack: str
    _hook_stage: str

    def __init__(self, hook: T, pack: str, stage: str) -> None:
        if type(pack) is not str or type(stage) is not str:
            raise TypeError("a hook's pack and stage are text")
        object.__setattr__(self, "_hook_object", hook)
        object.__setattr__(self, "_hook_pack", pack)
        object.__setattr__(self, "_hook_stage", stage)

    @property
    def pack(self) -> str:
        return self._hook_pack

    @property
    def stage(self) -> str:
        return self._hook_stage

    def call[R](self, run: Callable[[T], R]) -> R:
        """``run(hook object)``, which ends in a copier (``schema.copiers``), under the pass
        rule: what it returns, or ``PackFailed`` raised anew, with no context."""
        hook = self._hook_object
        ok, found = _contained(self._hook_pack, self._hook_stage, lambda: run(hook))
        if not ok:
            raise PackFailed(self._hook_pack, self._hook_stage)
        return cast(R, found)

    def enter[R](self, guarded: Callable[[T], R]) -> R:
        """``guarded(hook object)``, unguarded here: only the importer's own guard
        (``importers.checks.run_importer``, D385) is given it."""
        return guarded(self._hook_object)

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise TypeError("a hook's handle cannot be changed")

    def __delattr__(self, name: str) -> NoReturn:
        raise TypeError("a hook's handle cannot be changed")

    def __repr__(self) -> str:
        return f"<hook {self._hook_stage} of pack {self._hook_pack}>"

    def __copy__(self) -> "Hook[T]":
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> "Hook[T]":
        return self

    def __reduce__(self) -> NoReturn:
        raise TypeError("a hook cannot be pickled")


def contain[R](pack: str, stage: str, read: Callable[[], R]) -> tuple[bool, R | None]:
    """``read()`` under ``Hook.call``'s pass rule, for one item of what a hook gave: ``(True,
    what it returned)``, or ``(False, None)`` when it raised, logged. Only ``copiers.proposals``
    uses it, so that one proposal's failure skips that proposal alone (D249, D388)."""
    return _contained(pack, stage, read)


# --- Allowances ----------------------------------------------------------------------------------


class _NotJsonError(ValueError):
    """What a copy finds that JSON text cannot carry unchanged."""


class JsonTooLarge(ValueError):  # noqa: N818 - raised like a limit's refusal
    """What a pack gave that holds more than a copy allows: ``name`` the limit, ``most`` its
    value (D343)."""

    def __init__(self, name: str, most: int) -> None:
        super().__init__(f"more than {most} ({name})")
        self.name = name
        self.most = most


@dataclass
class _Allowance:
    """What a copy may still hold: JSON values, and characters of text (keys included), each
    string at most ``text`` of them; counted before each is copied. It records what the copy
    itself raised, so that only that is ever quoted or passed on as the copy's: an exception of
    the same type that is not it was raised by the code being copied (D343, D387)."""

    most_values: int
    most_characters: int
    text: int
    names: tuple[str, str, str]
    values: int = 0
    characters: int = 0
    tripped: JsonTooLarge | None = None
    """The limit this copy passed, raised as it is."""
    refused: _NotJsonError | None = None
    """What this copy found JSON text cannot carry unchanged, raised as it is."""
    reason: str = ""
    """``refused``'s reason, in the core's words."""

    @classmethod
    def unbounded(cls) -> "_Allowance":
        """An allowance of no limit but the recursion's, for a copy that only records."""
        most = sys.maxsize
        return cls(most, most, most, (RESULT_VALUES, RESULT_CHARACTERS, TEXT_CHARACTERS))

    def refuse(self, reason: str) -> _NotJsonError:
        """The exception that refuses what is being copied, for ``reason``, recorded."""
        self.reason = reason
        self.refused = _NotJsonError(reason)
        return self.refused

    def value(self) -> None:
        self.values += 1
        if self.values > self.most_values:
            self.trip(self.names[0], self.most_values)

    def string(self, length: int) -> None:
        if length > self.text:
            self.trip(self.names[2], self.text)
        self.characters += length
        if self.characters > self.most_characters:
            self.trip(self.names[1], self.most_characters)

    def trip(self, name: str, most: int) -> None:
        self.tripped = JsonTooLarge(name, most)
        raise self.tripped


def allowance(values: int, characters: int, text: int, names: tuple[str, str, str]) -> _Allowance:
    """A copy's allowance, which a site makes outside ``run`` for one call: at most ``values``
    JSON values and ``characters`` characters of text, each string at most ``text``, ``names``
    naming them (values, characters, text) when one is passed."""
    return _Allowance(values, characters, text, names)


class Allowances:
    """A set of allowances for one call, one issued per item of what its hook gave
    (``copiers.proposals``): a trip is honoured only for an allowance this set issued, found by
    identity. It keeps every allowance it issued alive, so that no ``id`` it recorded is reused
    while the set lives, and checks membership by ``id`` in O(1)."""

    __slots__ = ("_ids", "_issued", "_limits")

    def __init__(
        self, values: int, characters: int, text: int, names: tuple[str, str, str]
    ) -> None:
        self._limits = (values, characters, text, names)
        self._issued: list[_Allowance] = []
        self._ids: set[int] = set()

    def issue(self) -> _Allowance:
        """A new allowance, for one item."""
        values, characters, text, names = self._limits
        made = _Allowance(values, characters, text, names)
        self._issued.append(made)
        self._ids.add(id(made))
        return made

    def issued(self, given: _Allowance) -> bool:
        """Whether this set issued ``given``, by identity: its ``id`` among those of the
        allowances the set holds alive."""
        return id(given) in self._ids

    def tripped(self, given: _Allowance, error: BaseException) -> "Tripped | None":
        """``Tripped`` when ``error`` is the trip of ``given``, an allowance this set issued. The
        set's own check is defensive: ``copiers.proposals`` hands it only allowances the set
        issued, and it fails only for one another set issued (a test gives one)."""
        found = given.tripped
        if found is None or error is not found or not self.issued(given):
            return None
        return Tripped(found.name, found.most)


def allowances(values: int, characters: int, text: int, names: tuple[str, str, str]) -> Allowances:
    """A call's set of allowances (``Allowances``), which a site makes outside ``run``."""
    return Allowances(values, characters, text, names)


# --- What copiers return besides a copy ----------------------------------------------------------


@dataclass(frozen=True)
class Tripped:
    """A copy's own allowance passed: ``name`` the limit, ``most`` its value."""

    name: str
    most: int


@dataclass(frozen=True)
class NotJson:
    """A copy's own finding that what a hook gave is not JSON text carried unchanged."""


@dataclass(frozen=True)
class Late:
    """An analysis that returned past its call's deadline: nothing it gave was copied (D343)."""


__all__ = [
    "PASSED",
    "Allowances",
    "Hook",
    "JsonTooLarge",
    "Late",
    "NotJson",
    "PackFailed",
    "Tripped",
    "Unfit",
    "allowance",
    "allowances",
    "contain",
    "pack_failed",
    "passed",
]

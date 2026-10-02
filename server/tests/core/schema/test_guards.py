"""The one guard around every call of a pack's code (SPEC §10.1, D388): ``Hook.call``'s pass rule,
``contain``, the allowances and the handle's dunders."""

import copy
import gc
import logging
import pickle
import time
from typing import Any, NoReturn

import pytest

from aibi.core.schema import guards
from aibi.core.schema.guards import (
    Hook,
    JsonTooLarge,
    PackFailed,
    Tripped,
    allowance,
    allowances,
    contain,
)

SECRET = "s3cr3t-hook-text"


class _Meta(type):
    """A metaclass whose comparison and hash raise: looking its class up by ``==`` or in a dict
    runs the pack's code."""

    def __eq__(cls, other: object) -> bool:
        raise RuntimeError(SECRET)

    def __hash__(cls) -> int:
        raise RuntimeError(SECRET)


class _Owned(Exception, metaclass=_Meta):  # noqa: N818 - a pack names its own
    """An exception of a type whose metaclass runs code."""


class _Base(BaseException):
    """A ``BaseException`` subclass of the pack's own."""


class _Memory(MemoryError):  # noqa: N818 - a pack names its own
    pass


class _Interrupt(KeyboardInterrupt):
    pass


class _Exit(SystemExit):
    pass


RAISED: dict[str, BaseException] = {
    "value": ValueError(SECRET),
    "base": _Base(SECRET),
    "metaclass": _Owned(SECRET),
    "memory_subclass": _Memory(SECRET),
    "interrupt_subclass": _Interrupt(SECRET),
    "exit_subclass": _Exit(SECRET),
}
"""What a hook may raise that is its failure."""

PASSING: dict[str, tuple[BaseException, type[BaseException]]] = {
    "memory": (MemoryError(SECRET), MemoryError),
    "interrupt": (KeyboardInterrupt(SECRET), KeyboardInterrupt),
    "exit": (SystemExit(SECRET), SystemExit),
}
"""What passes, as a new instance of its own type."""


def _raising(error: BaseException) -> Any:
    def run(hook: object) -> NoReturn:
        raise error

    return run


@pytest.mark.parametrize("name", list(RAISED))
def test_whatever_a_hook_raises_is_its_failure_raised_anew_with_no_context(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    hook = Hook(object(), "library", "facet")
    with caplog.at_level(logging.WARNING), pytest.raises(PackFailed) as failed:
        hook.call(_raising(RAISED[name]))
    assert type(failed.value) is PackFailed
    assert (failed.value.pack, failed.value.stage, failed.value.limit) == ("library", "facet", None)
    assert failed.value.__context__ is None
    assert failed.value.__cause__ is None
    assert SECRET not in str(failed.value)
    assert SECRET not in caplog.text
    assert "_Owned" not in caplog.text
    assert "_Base" not in caplog.text
    assert "pack library: its facet raised" in caplog.text


@pytest.mark.parametrize("name", list(PASSING))
def test_a_passed_type_itself_passes_as_a_new_instance_outside_every_handler(name: str) -> None:
    given, kind = PASSING[name]
    hook = Hook(object(), "library", "facet")
    with pytest.raises(kind) as raised:
        hook.call(_raising(given))
    assert type(raised.value) is kind
    assert raised.value is not given
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    assert SECRET not in str(raised.value)
    if kind is SystemExit:
        assert raised.value.args == (1,)
    else:
        assert raised.value.args == ()


def test_the_log_names_a_built_in_type_alone(caplog: pytest.LogCaptureFixture) -> None:
    hook = Hook(object(), "library", "predicate")
    with caplog.at_level(logging.WARNING), pytest.raises(PackFailed):
        hook.call(_raising(KeyError(SECRET)))
    assert "pack library: its predicate raised KeyError" in caplog.text
    with caplog.at_level(logging.WARNING), pytest.raises(PackFailed):
        hook.call(_raising(guards.Unfit(SECRET)))
    assert "pack library: its predicate gave what the core does not take" in caplog.text
    assert SECRET not in caplog.text


class _OwnUnfit(guards.Unfit):
    """A pack's subclass of the core's own family."""


def test_the_core_s_own_family_alone_is_logged_as_what_it_does_not_take(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hook = Hook(object(), "library", "summary")
    foreign = guards._Foreign(SECRET)  # pyright: ignore[reportPrivateUsage]
    for raised, said in (
        (guards.Unfit(SECRET), "gave what the core does not take"),
        (foreign, "gave what the core does not take"),
        (_OwnUnfit(SECRET), "raised an exception of its own"),
    ):
        caplog.clear()
        with caplog.at_level(logging.WARNING), pytest.raises(PackFailed):
            hook.call(_raising(raised))
        assert caplog.messages == [f"pack library: its summary {said}"]


def test_call_returns_what_run_returns() -> None:
    held = object()
    assert Hook(held, "library", "facet").call(lambda h: h) is held


def test_contain_shares_the_pass_rule() -> None:
    for error in RAISED.values():
        assert contain("library", "proposer", lambda e=error: _raising(e)(None)) == (False, None)
    for given, kind in PASSING.values():
        with pytest.raises(kind) as raised:
            contain("library", "proposer", lambda g=given: _raising(g)(None))
        assert raised.value is not given
        assert raised.value.__context__ is None
    assert contain("library", "proposer", lambda: 3) == (True, 3)


# --- The handle ---------------------------------------------------------------------------------


class _Watched:
    """A hook object that records every attribute read and every operation on it."""

    def __init__(self) -> None:
        object.__setattr__(self, "reads", [])

    def __getattribute__(self, name: str) -> Any:
        if name != "reads":
            object.__getattribute__(self, "reads").append(name)
        return object.__getattribute__(self, name)

    def __eq__(self, other: object) -> bool:
        object.__getattribute__(self, "reads").append("__eq__")
        return self is other

    def __hash__(self) -> int:
        object.__getattribute__(self, "reads").append("__hash__")
        return id(self)

    def __repr__(self) -> str:
        object.__getattribute__(self, "reads").append("__repr__")
        return SECRET

    def __reduce__(self) -> Any:
        object.__getattribute__(self, "reads").append("__reduce__")
        return (_Watched, ())

    def __deepcopy__(self, memo: Any) -> Any:
        object.__getattribute__(self, "reads").append("__deepcopy__")
        return self


def test_a_handle_s_dunders_read_nothing_of_its_hook() -> None:
    watched = _Watched()
    hook = Hook(watched, "library", "facet")
    other = Hook(watched, "library", "facet")
    assert repr(hook) == "<hook facet of pack library>"
    assert hook == hook
    assert hook != other
    assert hash(hook) != hash(other) or hook is not other
    assert {hook: 1}[hook] == 1
    assert copy.copy(hook) is hook
    assert copy.deepcopy(hook) is hook
    assert copy.deepcopy({"a": [hook]})["a"][0] is hook
    with pytest.raises(TypeError, match="cannot be pickled"):
        pickle.dumps(hook)
    with pytest.raises(TypeError):
        hook._hook_pack = "other"  # type: ignore[misc]  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(TypeError):
        hook.extra = 1  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(TypeError):
        del hook._hook_object  # pyright: ignore[reportPrivateUsage]
    assert hook.pack == "library"
    assert hook.stage == "facet"
    assert object.__getattribute__(watched, "reads") == []
    assert not hasattr(hook, "__dict__")


def test_a_handle_s_pack_and_stage_are_exact_text() -> None:
    class Text(str):
        pass

    with pytest.raises(TypeError):
        Hook(object(), Text("library"), "facet")
    with pytest.raises(TypeError):
        Hook(object(), "library", Text("facet"))


def test_a_handle_keeps_no_state_of_its_calls() -> None:
    held = object()
    hook = Hook(held, "library", "facet")
    before = gc.get_referents(hook)
    hook.call(lambda h: 1)
    with pytest.raises(PackFailed):
        hook.call(_raising(ValueError(SECRET)))
    assert gc.get_referents(hook) == before
    assert all(not isinstance(one, BaseException) for one in before)


# --- Allowances ---------------------------------------------------------------------------------


def test_an_allowance_records_its_own_trip() -> None:
    given = allowance(2, 10, 5, ("a", "b", "c"))
    given.value()
    given.value()
    with pytest.raises(JsonTooLarge) as large:
        given.value()
    assert large.value is given.tripped
    assert (large.value.name, large.value.most) == ("a", 2)


def test_a_set_honours_only_the_trips_of_allowances_it_issued() -> None:
    issued = allowances(1, 10, 5, ("a", "b", "c"))
    own = issued.issue()
    stray = allowance(1, 10, 5, ("a", "b", "c"))
    for one in (own, stray):
        one.value()
        with pytest.raises(JsonTooLarge):
            one.value()
    assert own.tripped is not None
    assert stray.tripped is not None
    assert issued.tripped(own, own.tripped) == Tripped("a", 1)
    assert issued.tripped(stray, stray.tripped) is None
    assert issued.tripped(own, stray.tripped) is None
    assert issued.tripped(own, JsonTooLarge("a", 1)) is None
    other = issued.issue()
    assert other is not own
    assert other.values == 0


def test_a_set_finds_what_it_issued_by_identity_in_constant_time() -> None:
    issued = allowances(1, 10, 5, ("a", "b", "c"))
    own = [issued.issue() for _ in range(20_000)]
    strays = [allowance(1, 10, 5, ("a", "b", "c")) for _ in range(1_000)]
    started = time.monotonic()
    assert all(issued.issued(one) for one in own)
    assert time.monotonic() - started < 2
    assert not any(issued.issued(one) for one in strays)

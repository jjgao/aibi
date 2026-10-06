"""D399's static rule over ``src/aibi`` (``closure_rule.py``): the source obeys it, its boundary
sites are the checked-in table, every attribute name Pydantic gives an output is classified as
the run-time brute force finds it (``closure_brute.py``) and handled as classified, and each route
a mistake would take is found, by a fixture that trips that check alone."""

import json
import re
import subprocess
import sys
import time
import warnings
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError, field_validator
from tests.core import closure_brute
from tests.core import closure_rule as rule

from aibi.core.schema import output
from aibi.core.schema.output import Boundary, Output, TextSegment, admitted_at, text

TESTS = Path(__file__).resolve().parent.parent
N = closure_brute.N


def test_the_source_obeys_the_rule() -> None:
    findings, _ = rule.scan_source()
    assert findings == []


def test_the_boundary_sites_are_exactly_the_table() -> None:
    _, sites = rule.scan_source()
    assert rule.table_of(sites) == {
        member: tuple(sorted(places)) for member, places in rule.SITES.items()
    }
    assert set(rule.SITES) == set(Boundary)
    assert all(rule.SITES[member] for member in Boundary), "a member with no site is not listed"


def test_the_rule_reads_every_module_of_the_core_and_the_packs() -> None:
    read = set(rule.modules())
    assert read == set(rule.SOURCE.rglob("*.py"))
    assert any(path.is_relative_to(rule.SOURCE / "packs") for path in read)
    assert any(path.is_relative_to(rule.SOURCE / "core") for path in read)


def test_the_rule_runs_in_seconds() -> None:
    started = time.monotonic()
    rule.scan_source()
    assert time.monotonic() - started < 30


# --- (a) by name, whatever the receiver --------------------------------------------------------


def test_every_attribute_name_of_an_output_is_classified() -> None:
    names = {name for name in rule.reflected() if not name.startswith("__pydantic_")}
    assert sorted(names - set(rule.CLASSIFIED)) == []
    assert sorted(set(rule.CLASSIFIED) - names) == []
    assert {kind for kind, _ in rule.CLASSIFIED.values()} == {rule.HARMLESS, rule.BANNED}


def test_every_read_site_of_a_banned_name_is_used() -> None:
    findings, _ = rule.scan_source()
    assert findings == []
    for file, function, name in rule.READS:
        source = (rule.SOURCE / file).read_text()
        assert name in source, (file, function, name)
        without = {key: why for key, why in rule.READS.items() if key != (file, function, name)}
        saved = dict(rule.READS)
        rule.READS.clear()
        rule.READS.update(without)
        try:
            found, _ = rule.scan(source, file)
        finally:
            rule.READS.clear()
            rule.READS.update(saved)
        assert found, f"{file} {function} {name} is listed but not read"


def _flagged(source: str, file: str = "core/x.py") -> list[str]:
    return [finding.rule for finding in rule.scan(source, file)[0]]


def test_a_function_listed_in_imports_may_import_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "def load(name):\n    return importlib.import_module(name)"
    assert _flagged(source)
    monkeypatch.setitem(rule.IMPORTS, ("core/x.py", "load"), "the configured pack")
    assert _flagged(source) == []
    assert _flagged("def load(name):\n    return exec(name)")


@pytest.mark.parametrize(
    ("source", "name", "check"),
    [
        ("def f(m):\n    m.model_config['frozen'] = False", "model_config", "a write through"),
        ("def f(m):\n    m.__dict__['text'] = 'x'", "__dict__", "a write through"),
        ("def f(m):\n    vars(m)['text'] = 'x'", "vars", "a write through vars()"),
    ],
    ids=["model_config", "__dict__", "vars"],
)
def test_a_write_through_a_listed_read_is_still_flagged(
    source: str, name: str, check: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read site of ``READS`` reads: the write of a member through it is flagged on its own."""
    assert _flagged(source)
    monkeypatch.setitem(rule.READS, ("core/x.py", "f", name), "a read")
    if name == "vars":
        monkeypatch.setitem(rule.READS, ("core/x.py", "f", "__dict__"), "a read")
    found = _flagged(source)
    assert len(found) == 1, found
    assert check in found[0]


def test_every_listed_site_of_named_and_context_reads_is_used() -> None:
    for table, source in (
        (rule.NAMED, "getattr(x, name)"),
        (rule.CONTEXT_READS, "info.context"),
        (rule.DUMPS_IN_VALIDATION, "dumped_in_validation(x)"),
    ):
        for file, function in list(table):
            assert function.split(".")[-1] in (rule.SOURCE / file).read_text(), (file, function)
            saved = dict(table)
            table.pop((file, function))
            try:
                found, _ = rule.scan((rule.SOURCE / file).read_text(), file)
            finally:
                table.clear()
                table.update(saved)
            assert found, f"{file} {function} is listed but not used ({source})"


# --- (a) the table is the run-time brute force -------------------------------------------------


@pytest.fixture(scope="module")
def made() -> dict[str, list[str]]:
    """What the brute force made text with, by name: a child process attacks the types."""
    ran = subprocess.run(
        [sys.executable, "-m", "tests.core.closure_brute"],
        cwd=TESTS.parent,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr[-2000:] or ran.stdout[-2000:]
    return json.loads(ran.stdout)


def disagreements(made: dict[str, list[str]]) -> list[str]:
    """The names the table classifies otherwise than the brute force finds. A name that makes
    text is banned (``__pydantic_*`` names are banned whatever); one that does not is harmless."""
    wrong: list[str] = []
    for name in sorted(rule.reflected()):
        if name.startswith("__pydantic_"):
            if name in made and not rule.banned(name):
                wrong.append(name)
        elif rule.banned(name) != (name in made):
            wrong.append(name)
    return wrong


def test_the_table_agrees_with_the_brute_force(made: dict[str, list[str]]) -> None:
    assert made, "the brute force made no text: it is not attacking"
    assert disagreements(made) == []
    assert "__pydantic_decorators__" in made
    # What the type closes makes no text, and is harmless (a test of each is in test_closure.py).
    assert {"copy", "_copy_and_set_values", "construct", "parse_obj"}.isdisjoint(made)


def _flipped(name: str) -> tuple[str, str]:
    kind, why = rule.CLASSIFIED[name]
    return (rule.HARMLESS if kind == rule.BANNED else rule.BANNED, why)


@pytest.mark.parametrize("name", sorted(rule.CLASSIFIED))
def test_flipping_any_classified_name_disagrees_with_the_brute_force(
    name: str, made: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(rule.CLASSIFIED, name, _flipped(name))
    assert disagreements(made) == [name]


def test_the_brute_force_notices_a_name_that_is_not_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The brute force is not a table: undo a closure of the type, and its name makes text."""
    monkeypatch.setattr(Output, "copy", output.BaseModel.copy, raising=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # Pydantic 1's API warns, and the suite turns it to errors
        made_now = closure_brute.attack(["copy"])
    assert "copy" in made_now


class _ClassOnly:
    """An attribute only the class has: reading it from an instance raises."""

    def __get__(self, instance: object, owner: type) -> Callable[..., object]:
        if instance is not None:
            raise AttributeError("synthetic")
        return lambda *args, **kwargs: [text(N)]


def test_the_brute_force_reads_a_name_at_the_class_and_looks_into_what_it_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A synthetic name that only the class has, and returns a list holding text made out of the
    look-alike: found through the owner ``class``, and by looking into the container. Neither
    owner nor look is redundant (the instance's and the subject's checks do not cover them)."""
    monkeypatch.setattr(TextSegment, "synthetic_route", _ClassOnly(), raising=False)
    made_now = closure_brute.attack(["synthetic_route"])
    assert set(made_now) == {"synthetic_route"}
    assert all(" class " in found for found in made_now["synthetic_route"])


def test_the_brute_force_reads_a_name_of_a_type_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """A name only a ``TypeAdapter`` has, that returns text made out of the look-alike: found
    through the owner ``adapter``, so the adapters of the types are attacked as the types are."""
    monkeypatch.setattr(
        TypeAdapter, "synthetic_route", staticmethod(lambda *a, **k: [text(N)]), raising=False
    )
    made_now = closure_brute.attack(["synthetic_route"])
    assert set(made_now) == {"synthetic_route"}
    assert all(" adapter " in found for found in made_now["synthetic_route"])


def test_the_brute_force_notices_a_type_adapter_that_takes_a_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Takes:
        def validate_python(self, value: object) -> TextSegment:
            return text(N)

    assert not closure_brute.mapping_taken_by_an_adapter()
    monkeypatch.setitem(closure_brute.ADAPTERS, TextSegment, _Takes())  # type: ignore[arg-type]
    assert closure_brute.mapping_taken_by_an_adapter()


@pytest.mark.parametrize("name", sorted(rule.reflected()))
def test_each_name_is_flagged_as_classified(name: str) -> None:
    """Each attribute name of ``BaseModel``, ``Output`` and ``TextSegment``: a banned one is
    flagged read, called or fetched by ``getattr``; a harmless one is not; and every one is
    flagged assigned, deleted, or named by ``setattr`` or ``delattr``."""
    read = [
        f"x.{name}",
        f"x.{name}()",
        f"y = x.y.{name}",
        f"getattr(x, {name!r})",
        f"match s:\n    case object({name}=d):\n        pass",  # a bare identifier (review 2)
        f"f({name}=1)",
        f"import m as {name}",
    ]
    for source in read:
        assert bool(_flagged(source)) == rule.banned(name), source
    for source in (
        f"x.{name} = 1",
        f"x.y.{name} = 1",
        f"del x.{name}",
        f"setattr(x, {name!r}, 1)",
        f"delattr(x, {name!r})",
    ):
        assert _flagged(source), source


CAP = "admitted_at(Boundary.CLI_CLIENT)"
"""The capability as a boundary site writes it."""

STATIC = {
    "object.__setattr__ on an output": "object.__setattr__(segment, 'text', n)",
    "object.__setattr__ outside __post_init__": (
        "@dataclass(frozen=True)\nclass A:\n    a: int\n    def f(self):\n"
        "        object.__setattr__(self, 'a', 1)"
    ),
    "object.__setattr__ of an undeclared member": (
        "@dataclass(frozen=True)\nclass A:\n    a: int\n    def __post_init__(self):\n"
        "        object.__setattr__(self, 'b', 1)"
    ),
    "object.__setattr__ in a dataclass that is not frozen": (
        "@dataclass\nclass A:\n    a: int\n    def __post_init__(self):\n"
        "        object.__setattr__(self, 'a', 1)"
    ),
    "__dict__ written": "segment.__dict__['text'] = n",
    "__dict__ read elsewhere": "found = segment.__dict__",
    "vars() written": "vars(segment)['text'] = n",
    "vars() updated": "vars(segment).update(text=n)",
    "__class__ assigned": "fake.__class__ = TextSegment",
    "__class__ by setattr": "setattr(fake, '__class__', TextSegment)",
    "setattr with a computed name": "setattr(fake, name, TextSegment)",
    "delattr with a computed name": "delattr(fake, name)",
    "BaseModel's unbound method": "BaseModel.model_copy(segment, update={'text': n})",
    "model_construct": "Refusal.model_construct(message=[{'text': n}])",
    "another class's __setstate__": "DataSegment.__setstate__(segment, state)",
    "Output's __setstate__": "Output.__setstate__(segment, state)",
    "validate_assignment": "segment.__pydantic_validator__.validate_assignment(segment, 'text', n)",
    "model_config written": "segment.model_config['frozen'] = False",
    "model_config of the type": "type(segment).model_config.update(frozen=False)",
    "Pydantic 1's copy with update": "segment.copy(update={'text': n})",
    "an output's attribute assigned": (
        "from aibi.core.schema.output import TextSegment\nTextSegment.model_validate = f"
    ),
    "the output module's function assigned": (
        "from aibi.core.schema import output\noutput.text = f"
    ),
    "the output module's function by setattr": (
        "from aibi.core.schema import output as om\nsetattr(om, 'text', f)"
    ),
    "the output module's private name": "import aibi.core.schema.output as om\nom._BY_TEXT",
    "ctypes": "import ctypes",
    "gc": "from gc import get_referrers",
    # strings, computed names and aliases (round 1, m1)
    "a banned name as a string": "getattr(segment, '__dict__')['text'] = n",
    "a banned name in a match pattern": (
        "match segment:\n    case object(__dict__=d):\n        d['text'] = n"
    ),
    "a banned name in a match pattern, a state": (
        "match segment:\n    case object(__pydantic_fields__=d):\n        d.clear()"
    ),
    "a banned name as a keyword": "f(__dict__=segment)",
    "a banned name as an alias": "import copy as __getstate__",
    "a banned name as a from-import alias": "from copy import deepcopy as __reduce_ex__",
    "a banned name as a parameter": "def g(__dict__):\n    return __dict__",
    "a banned name bound by a pattern": (
        "match segment:\n    case {'a': 1, **__dict__}:\n        pass"
    ),
    "a banned name bound by a star pattern": "match s:\n    case [*__dict__]:\n        pass",
    "a banned name bound by as": "match s:\n    case int() as __dict__:\n        pass",
    "a banned name declared global": "def g():\n    global __dict__",
    "a capability name in a match pattern": (
        "match s:\n    case object(admitted_at=a):\n        pass"
    ),
    "a private capability name as a keyword": "f(_BY_TEXT=1)",
    "the context read by a pattern": "match info:\n    case object(context=c):\n        pass",
    "a private name as a string": "cap = getattr(output, '_BY_TEXT')",
    "a public name as a string": "make = getattr(output, 'admitted_at')",
    "the output module as a string": "m = importlib.import_module('aibi.core.schema.output')",
    "a banned name by __getattribute__": "object.__getattribute__(segment, '__dict__')['text'] = n",
    "a banned name by attrgetter": "operator.attrgetter('__dict__')(segment)['text'] = n",
    "a banned name by getattr_static": "inspect.getattr_static(segment, '__dict__')",
    "a name that is computed, by getattr": "getattr(segment, name)['text'] = n",
    "a name that is computed, by attrgetter": "operator.attrgetter(name)(segment)",
    "a name that is computed, by methodcaller": "operator.methodcaller(name)(segment)",
    "a name that is computed, by __getattribute__": "segment.__getattribute__(name)",
    "setattr aliased": "sa = setattr\nsa(segment, '__dict__', {'text': n})",
    "delattr aliased": "delete = delattr",
    "vars aliased": "v = vars\nv(segment)['text'] = n",
    "getattr aliased": "g = getattr",
    "getattr passed on": "list(map(getattr, segments, names))",
    "builtins' setattr": "builtins.setattr(segment, '__dict__', {'text': n})",
    "object.__setattr__ aliased": "osa = object.__setattr__",
    "exec": "exec(code)",
    "eval": "eval(code)",
    "compile": "compile(code, 'f', 'exec')",
    "__import__": "m = __import__(name)",
    "import_module": "m = importlib.import_module(name)",
    "a mapping of options given to a validating call": (
        "TextSegment.model_validate(v, **{'context': info.context})"
    ),
    "a name of options given to a validating call": (
        "options = {'context': c}\nx = M.model_validate(v, **options)"
    ),
    "a mapping given to copy": "segment.copy(**{'update': {'text': n}})",
    "a mapping given to model_copy": "segment.model_copy(**{'update': {'text': n}})",
    "a mapping given to partial": "functools.partial(M.model_validate, **{'context': c})",
    "a star given to a validating call": "M.model_validate(*args)",
    "options given by position": "TypeAdapter(M).validate_python(v, None, None, c)",
    "a validator's context stashed": (
        "def g(v, info):\n    saved.append(info.context)\n    return v"
    ),
    "a validator's context read by getattr": (
        "def g(v, info):\n    return getattr(info, 'context')"
    ),
    "a TextSegment built at module level": (
        "UNSHOWN = TextSegment(text='(a name the core does not show)')"
    ),
    "a TextSegment built by a call, aliased": (
        "from aibi.core.schema.output import TextSegment as T\nUNSHOWN = T(text='x')"
    ),
    "a TextSegment validated by a call": "x = TextSegment.model_validate({'text': n})",
    # (b) the capability and the shape
    "the capability kept": f"found = {CAP}",
    "the capability passed on": "x = M.model_validate(v, context=given)",
    "a validator's context forwarded": "x = M.model_validate(v, context=info.context)",
    "a call in an argument": f"x = M.model_validate(f(v), context={CAP})",
    "a call in the callee": f"x = f().model_validate(v, context={CAP})",
    "another keyword": (f"x = M.model_validate(v, from_attributes=True, context={CAP})"),
    "an extra keyword after the context": (
        f"x = M.model_validate(v, context={CAP}, from_attributes=True)"
    ),
    "an extra keyword strict=False": f"x = M.model_validate(v, context={CAP}, strict=False)",
    "an extra keyword by_alias": f"x = M.model_validate_json(v, context={CAP}, by_alias=True)",
    "an extra keyword on validate_python": (
        f"x = TypeAdapter(M).validate_python(v, context={CAP}, from_attributes=True)"
    ),
    "another method": f"x = M.model_construct(v, context={CAP})",
    "a comprehension": f"x = [M.model_validate(v, context={CAP}) for v in w]",
    "a lambda": f"f = lambda v: M.model_validate(v, context={CAP})",
    "an async def": (f"async def f(v):\n    return M.model_validate(v, context={CAP})"),
    "a yield": f"def f(v):\n    yield M.model_validate(v, context={CAP})",
    "a walrus": f"if (x := M.model_validate(v, context={CAP})):\n    pass",
    "two targets": f"x = y = M.model_validate(v, context={CAP})",
    "an attribute target": f"x.y = M.model_validate(v, context={CAP})",
    "a member that is not one": "x = M.model_validate(v, context=admitted_at(Boundary.NOWHERE))",
    "a computed member": "x = M.model_validate(v, context=admitted_at(member))",
    "the capability under another name": (
        "from aibi.core.schema.output import admitted_at as a\nx = M.model_validate(v, context=a)"
    ),
    "the private capability imported": "from aibi.core.schema.output import _Admitted",
    "unpickled with a call": "x = unpickled(Boundary.READER_ANSWER, U(f))",
    "unpickled kept": "load = unpickled",
    # the helper that dumps without validating again
    "a caller of dumped_in_validation that is not listed": (
        "def serve(o):\n    return dumped_in_validation(o, mode='json')"
    ),
    "dumped_in_validation by the module": (
        "def serve(o):\n    return output.dumped_in_validation(o)"
    ),
    "dumped_in_validation aliased": "d = dumped_in_validation",
    "dumped_in_validation imported under another name": (
        "from aibi.core.schema.output import dumped_in_validation as dump"
    ),
    "dumped_in_validation by getattr": "make = getattr(output, 'dumped_in_validation')",
    # (c) routes
    "a route that returns a model": (
        "@router.get('/x', response_model=X)\ndef x() -> X:\n    return X()"
    ),
    "a route that returns a model or a response": (
        "@router.get('/x')\ndef x() -> X | Response:\n    return X()"
    ),
    "an endpoint of a model": (
        "def _endpoint() -> Callable[[Request], Awaitable[X]]:\n    ...\n"
        "router.add_api_route('/x', _endpoint(), methods=['POST'])"
    ),
    "an endpoint of a model or a response": (
        "def _endpoint() -> Callable[[Request], Awaitable[X | Response]]:\n    ...\n"
        "router.add_api_route('/x', _endpoint(), methods=['POST'])"
    ),
}
"""Spellings of the routes that skip the guard, and of boundary sites not of the shape."""


@pytest.mark.parametrize("source", list(STATIC.values()), ids=list(STATIC))
def test_each_static_fixture_is_flagged(source: str) -> None:
    assert _flagged(source)


ONLY = {
    "BaseModel.<attribute>": (
        "pydantic.BaseModel.model_copy(segment, update={'text': n})",
        "BaseModel.model_copy skips",
    ),
    "BaseModel.<attribute> by name": (
        "BaseModel.model_copy(segment, update={'text': n})",
        "BaseModel.model_copy skips",
    ),
    "two-argument super": (
        "super(Output, segment).model_copy(update={'text': n})",
        "two-argument super",
    ),
    "an attribute of the output module assigned": (
        "import aibi.core.schema.output as om\nom.text = f",
        "an attribute of om assigned",
    ),
    "an attribute of the output module assigned, by from-import": (
        "from aibi.core.schema import output as om\nom.text = f",
        "an attribute of om assigned",
    ),
    "an attribute of an exported class assigned": (
        "from aibi.core.schema.output import DataSegment as D\nD.helper = f",
        "an attribute of D assigned",
    ),
    "setattr on the output module": (
        "import aibi.core.schema.output as om\nsetattr(om, 'text', f)",
        "setattr on om",
    ),
    "a validation context read": ("info.context", "a validation context read"),
    "a banned name in a match pattern": (
        "match s:\n    case object(__dict__=d):\n        pass",
        "the banned name __dict__ as an identifier",
    ),
    "a banned name as a keyword": ("f(__setstate__=1)", "the banned name __setstate__ as"),
    "a banned name as an import alias": (
        "import copy as __getstate__",
        "the banned name __getstate__ as",
    ),
    "a banned name as a parameter": (
        "def g(__reduce__):\n    pass",
        "the banned name __reduce__ as",
    ),
    "a capability name in a pattern": (
        "match s:\n    case object(Boundary=b):\n        pass",
        "the capability's name Boundary as an identifier",
    ),
    "the context read by a pattern": (
        "match info:\n    case object(context=c):\n        pass",
        "a validation context read by a pattern",
    ),
    "a context that is not a dict literal": (
        "x = M.model_validate(v, context=holder.kept)",
        "a context that is not a dict literal",
    ),
    "the capability imported under another name": (
        "from aibi.core.schema.output import admitted_at as make",
        "admitted_at imported under another name",
    ),
    "a caller of dumped_in_validation that is not listed": (
        "def serve(o):\n    return dumped_in_validation(o)",
        "dumped_in_validation outside DUMPS_IN_VALIDATION",
    ),
    "dumped_in_validation by the module": (
        "def serve(o):\n    return output.dumped_in_validation(o)",
        "dumped_in_validation outside DUMPS_IN_VALIDATION",
    ),
    "dumped_in_validation imported under another name": (
        "from aibi.core.schema.output import dumped_in_validation as dump",
        "dumped_in_validation imported under another name",
    ),
    "dumped_in_validation by a string": (
        "make = getattr(output, 'dumped_in_validation')",
        "spells a name of the capability",
    ),
    "a TextSegment built by a call": ("TextSegment(text='x')", "a TextSegment built"),
    "a TextSegment built by a call, as an alias": (
        "from aibi.core.schema.output import TextSegment as T\nT(text='x')",
        "a TextSegment built",
    ),
    "a mapping given to a validating call": ("M.model_validate(v, **o)", "* or **"),
    "options by position": ("M.validate_json(v, 1)", "by position"),
    "getattr with a computed name": ("getattr(x, n)", "a name that is not a constant"),
    "getattr_static with a computed name": (
        "inspect.getattr_static(x, n)",
        "a name that is not a constant",
    ),
    "an alias of vars": ("v = vars", "vars used as a value"),
    "an alias of getattr passed on": ("map(getattr, xs, ns)", "getattr used as a value"),
    "an alias by an attribute": ("g = builtins.getattr", "getattr used as a value"),
    "exec": ("exec(c)", "exec, which runs"),
    "import_module": ("importlib.import_module(n)", "import_module, which"),
    "a route of a model or a response": (
        "@router.get('/x')\ndef x() -> X | Response:\n    ...",
        "a route that is not a Response",
    ),
}
"""A fixture for each check that trips that check alone (the other checks, by a harmless name or
none, leave it): so that removing a check is a failure of its own fixture."""


@pytest.mark.parametrize(("source", "check"), list(ONLY.values()), ids=list(ONLY))
def test_each_check_has_a_fixture_that_trips_it_alone(source: str, check: str) -> None:
    found = _flagged(source)
    assert len(found) == 1, found
    assert check in found[0]


def test_dumped_in_validation_is_called_only_where_listed() -> None:
    """The helper skips the dump's check of a mutable part: a capability (D399). A listed
    function calls it by its name; it is not a value there, nor a call anywhere else."""
    file, function = "core/schema/results.py", "_check_digest"
    assert (file, function) in rule.DUMPS_IN_VALIDATION
    assert _flagged(f"def {function}(o):\n    return dumped_in_validation(o)", file) == []
    assert _flagged("def other(o):\n    return dumped_in_validation(o)", file)
    [aliased] = _flagged(f"def {function}(o):\n    d = dumped_in_validation", file)
    assert "used as a value" in aliased
    [passed] = _flagged(f"def {function}(o):\n    return map(dumped_in_validation, o)", file)
    assert "used as a value" in passed
    assert _flagged(f"def {function}(o):\n    return dumped_in_validation(o)", "core/other.py")


def _capability_names() -> frozenset[str]:
    """The private names of ``output`` that hold or make a capability, by reflection: the class,
    an instance, a table of them, the context variable of an unpickling, and the protocol of what
    unpickles."""
    found: set[str] = set()
    for name, value in vars(output).items():
        if not name.startswith("_") or name.startswith("__"):
            continue
        if (
            value is output._Admitted  # pyright: ignore[reportPrivateUsage]
            or isinstance(value, output._Admitted)  # pyright: ignore[reportPrivateUsage]
            or (
                isinstance(value, dict)
                and value
                and all(isinstance(held, output._Admitted) for held in value.values())  # pyright: ignore[reportPrivateUsage]
            )
            or (isinstance(value, ContextVar) and value.name == "aibi_unpickling")
            or getattr(value, "__name__", "") == "_Loads"
        ):
            found.add(name)
    return frozenset(found)


def test_the_private_names_are_the_capability_objects_of_output() -> None:
    assert _capability_names() == rule.PRIVATE_CAPABILITY


@pytest.mark.parametrize("name", sorted(rule.PRIVATE_CAPABILITY))
def test_each_private_name_has_fixtures_that_pin_it(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spelled as a name, an import and a string: each flagged, and not once the name is off the
    list, so the list has no entry that nothing tests."""
    spellings = (
        f"x = {name}",
        f"from aibi.core.schema.output import {name}",
        f"getattr(output, {name!r})",
    )
    for source in spellings:
        assert any("private name" in found or "spells" in found for found in _flagged(source)), (
            source
        )
    monkeypatch.setattr(rule, "PRIVATE_CAPABILITY", rule.PRIVATE_CAPABILITY - {name})
    for source in spellings:
        assert not any(
            "private name" in found or "spells a name of the capability" in found
            for found in _flagged(source)
        ), source


# --- the same spellings at run time ---------------------------------------------------------------


class _Forwards(Output):
    x: str

    @field_validator("x")
    @classmethod
    def _forward(cls, value: str, info: Any) -> str:
        _KEPT.append(TextSegment.model_validate({"text": N}, **{"context": info.context}))
        return value


_KEPT: list[TextSegment] = []


def _forwarded() -> object:
    _KEPT.clear()
    _Forwards.model_validate({"x": "a"}, context=admitted_at(Boundary.CLI_CLIENT))
    return _KEPT[-1]


def _dumped_text(made_by: object) -> bool:
    return isinstance(made_by, TextSegment) and made_by.text == N


def _by_dict() -> object:
    made_by = text("harmless")
    object.__getattribute__(made_by, "__dict__")["text"] = N
    return made_by


def _by_attrgetter() -> object:
    import operator

    made_by = text("harmless")
    operator.attrgetter("__dict__")(made_by)["text"] = N
    return made_by


def _by_vars() -> object:
    v = vars
    made_by = text("harmless")
    v(made_by)["text"] = N
    return made_by


def _by_getattr() -> object:
    return TextSegment.model_validate({"text": N}, context=output._BY_TEXT)


def _by_setattr_alias() -> object:
    sa = setattr
    made_by = text("harmless")
    sa(made_by, "__dict__", {"text": N})
    return made_by


def _by_copy() -> object:
    return text("harmless").copy(**{"update": {"text": N}})  # pyright: ignore[reportDeprecated]


def _by_dict_options() -> object:
    options: dict[str, Any] = {"context": "not the capability"}
    return TextSegment.model_validate({"text": N}, **options)


RUNTIME_OF_STATIC: dict[str, tuple[str, Callable[[], object], bool]] = {
    "a validator forwards its context": (
        "TextSegment.model_validate(v, **{'context': info.context})",
        _forwarded,
        False,
    ),
    "getattr of a private name": ("cap = getattr(output, '_BY_TEXT')", _by_getattr, False),
    "__getattribute__ of __dict__": (
        "object.__getattribute__(segment, '__dict__')['text'] = n",
        _by_dict,
        False,
    ),
    "attrgetter of __dict__": (
        "operator.attrgetter('__dict__')(segment)['text'] = n",
        _by_attrgetter,
        False,
    ),
    "an alias of vars": ("v = vars\nv(segment)['text'] = n", _by_vars, False),
    "an alias of setattr": (
        "sa = setattr\nsa(segment, '__dict__', {'text': n})",
        _by_setattr_alias,
        False,
    ),
    "copy with a mapping": ("segment.copy(**{'update': {'text': n}})", _by_copy, True),
    "a context that is not the capability": (
        "TextSegment.model_validate(v, **{'context': c})",
        _by_dict_options,
        True,
    ),
}
"""Each spelling the rule must flag, with what it does at run time: the type refuses it (``True``,
and the rule is a second wall), or it serves the text (``False``), which only the rule stops, so
that the rule's check is load-bearing for it."""


@pytest.mark.parametrize(
    ("source", "run", "closed"), list(RUNTIME_OF_STATIC.values()), ids=list(RUNTIME_OF_STATIC)
)
def test_each_spelling_is_flagged_and_the_type_refuses_it_or_only_the_rule_does(
    source: str, run: Callable[[], object], closed: bool
) -> None:
    assert _flagged(source)
    if closed:
        with pytest.raises((TypeError, ValidationError)):
            run()
    else:
        assert _dumped_text(run())


SHAPES = {
    "an assignment": f"x = M.model_validate(v, context={CAP})",
    "a return": f"def f(v):\n    return a.b.validate_json(v[1], context={CAP})",
    "an expression": f"M.model_validate_json(v.w, context={CAP})",
    "unpickled": "x = unpickled(Boundary.READER_ANSWER, unpickler)",
    "a frozen dataclass's own member": (
        "@dataclass(frozen=True)\nclass A:\n    a: int\n    def __post_init__(self):\n"
        "        object.__setattr__(self, 'a', 1)"
    ),
    "a dict context": "x = M.model_validate(v, context={'parsed': True})",
    "a route of a Response": "@router.get('/x')\ndef x() -> Response:\n    return Response()",
}


@pytest.mark.parametrize("source", list(SHAPES.values()), ids=list(SHAPES))
def test_each_allowed_shape_passes(source: str) -> None:
    assert _flagged(source) == []


def test_a_new_site_reusing_a_member_is_not_the_table() -> None:
    added = "def g(v):\n    x = M.model_validate(v, context=admitted_at(Boundary.CLI_CLIENT))\n"
    _, sites = rule.scan_source()
    _, more = rule.scan(added, "core/store/proposals.py")
    assert more
    assert rule.table_of(sites + more) != rule.table_of(sites)


# --- the tests' own use of the capability (m8) ---------------------------------------------------

CAPABILITY_IN_TESTS: dict[str, str] = {
    "core/_segments.py": "the helpers, each one validating call of golden JSON",
    "core/closure_rule.py": "the rule names the capability",
    "core/test_closure_rule.py": "the rule's fixtures",
    "core/schema/test_closure.py": "the guard's tests",
    "core/test_closure_boundaries.py": "the boundaries' tests",
    "core/importers/test_reader_segments.py": "unpickles what a child sends, as the reader does",
    "core/api/test_config.py": "a validator that reads the context assumes no dict: it may be one",
}
"""The test modules that name the capability, each with why; no fixture or conftest does."""


def test_no_test_holds_the_capability_but_those_listed() -> None:
    named = re.compile(
        r"\b(admitted_at|unpickled)\(|\b(" + "|".join(sorted(rule.PRIVATE_CAPABILITY)) + r")\b"
    )
    found = {
        path.relative_to(TESTS).as_posix()
        for path in TESTS.rglob("*.py")
        if named.search(path.read_text())
    }
    assert found == set(CAPABILITY_IN_TESTS)
    assert not any("conftest" in name for name in found)

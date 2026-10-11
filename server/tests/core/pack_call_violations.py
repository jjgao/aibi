"""One violation per rule of ``test_pack_calls`` (D403), as source text: each case is the rule it
breaks, the role of its module (``site``, ``copiers``, ``guards`` or ``importer-run``) and a module
that breaks that rule alone. The plan's and its reviews' escapes are among them: ``h`` used twice
(an allowance read off the hook), a hoisted or shared allowance, an aliased or shadowed copier, a
memoised view, a handler in a run, ``Hook.call(h, run)``, ``partial`` and ``getattr``; and round 1's
of the code: ``methodcaller``, a computed name read off a hook, ``__getattribute__``, and a
``PackView`` or ``PackView.of`` rebound or aliased; and round 2's (m5): ``methodcaller`` and
``attrgetter`` aliased or held, ``contain`` by attribute or aliased, ``functools``' caches aliased,
a module-level binding written by a copier, and a handler's name rebound or aliased."""

_IMPORTS = """
from functools import partial

from aibi.core.engine.resolve import PackView
from aibi.core.schema import copiers, guards
from aibi.core.schema.copiers import caveat_codes, expansion, is_true, values
"""


def _site(body: str) -> str:
    return _IMPORTS + body


CASES: dict[str, tuple[str, str, str]] = {
    # --- call: .call only ever called at once, with one run ---
    "call as a value": (
        "call",
        "site",
        _site("""
def site(hook, code):
    run = hook.call
    return run(lambda h: is_true(h, code))
"""),
    ),
    "call through partial": (
        "call",
        "site",
        _site("""
def site(hook, code):
    return partial(hook.call)(lambda h: is_true(h, code))
"""),
    ),
    "call with the handle as an argument": (
        "call",
        "site",
        _site("""
def site(Hook, hook, code):
    return Hook.call(hook, lambda h: is_true(h, code))
"""),
    ),
    "call through getattr": (
        "call",
        "site",
        _site("""
def site(hook, code):
    return getattr(hook, "call")
"""),
    ),
    "call through methodcaller": (
        "call",
        "site",
        _site("""
import operator


def site(hook, code):
    return operator.methodcaller("call", lambda h: is_true(h, code))(hook)
"""),
    ),
    "a hook's method through methodcaller": (
        "call",
        "site",
        _site("""
from operator import methodcaller


def site(validator, view):
    return methodcaller("validate_descriptors", view)(validator)
"""),
    ),
    "methodcaller imported under another name": (
        "call",
        "site",
        _site("""
from operator import methodcaller as mc


def site(hook, code):
    return mc("call", lambda h: is_true(h, code))(hook)
"""),
    ),
    "methodcaller held": (
        "call",
        "site",
        _site("""
import operator


def site(hook, code):
    m = operator.methodcaller
    return m("call", lambda h: is_true(h, code))(hook)
"""),
    ),
    "attrgetter held": (
        "call",
        "site",
        _site("""
import operator

GET = operator.attrgetter


def site(hook):
    return GET("compile")(hook)
"""),
    ),
    "methodcaller named as text": (
        "call",
        "site",
        _site("""
import operator


def site(hook, name):
    return getattr(operator, "methodcaller")(name)(hook)
"""),
    ),
    "a computed name read off a hook": (
        "call",
        "site",
        _site("""
def site(registry, code):
    hook = registry.ontology_validator("marks")
    return getattr(hook, "ca" + "ll")(lambda h: is_true(h, code))
"""),
    ),
    "a computed name read off a handle parameter": (
        "call",
        "site",
        _site("""
def site(hooks: "list[Hook[Facet]]", name, view):
    for hook in hooks:
        getattr(hook, name)(lambda h: is_true(h, view))
"""),
    ),
    # --- getattribute: no reference to __getattribute__ outside guards ---
    "a hook's call through __getattribute__": (
        "getattribute",
        "site",
        _site("""
def site(hook, code):
    return hook.__getattribute__("ca" "ll")(lambda h: is_true(h, code))
"""),
    ),
    "object.__getattribute__ named as text": (
        "getattribute",
        "site",
        """
def site(hook):
    return getattr(object, "__getattribute__")(hook, "x")
""",
    ),
    # --- run: a lambda or a nested def of one parameter, its body a copier's call ---
    "run that is a module function": (
        "run",
        "site",
        _site("""
def run(h):
    return is_true(h, 1)


def site(hook):
    return hook.call(run)
"""),
    ),
    "run with a default": (
        "run",
        "site",
        _site("""
def site(hook, code):
    return hook.call(lambda h, code=code: is_true(h, code))
"""),
    ),
    "run that calls the hook itself": (
        "run",
        "site",
        _site("""
def site(hook, code):
    return hook.call(lambda h: h(code) if True else is_true(h, code))
"""),
    ),
    "run with a handler": (
        "run",
        "site",
        _site("""
def site(hook, code):
    def run(h):
        try:
            return is_true(h, code)
        except ValueError:
            return False

    return hook.call(run)
"""),
    ),
    # --- hook: the parameter once, first, alone or as h.METHOD ---
    "hook used twice, an allowance read off it": (
        "hook",
        "site",
        _site("""
def site(hook, inputs):
    return hook.call(lambda h: values(h.run, inputs, allowance=h.budget, ends=None))
"""),
    ),
    "hook not first": (
        "hook",
        "site",
        _site("""
def site(hook, code):
    return hook.call(lambda h: is_true(code, h))
"""),
    ),
    # --- argument: the other arguments are names or literals ---
    "an argument computed inside the run": (
        "argument",
        "site",
        _site("""
def site(hook, release):
    return hook.call(lambda h: is_true(h, release.descriptors))
"""),
    ),
    "an argument spread": (
        "argument",
        "site",
        _site("""
def site(hook, given):
    return hook.call(lambda h: is_true(h, **given))
"""),
    ),
    # --- copier: one of COPIERS, imported unaliased, global, bound once ---
    "a copier that is not one": (
        "copier",
        "site",
        _site("""
def own(h, code):
    return h(code)


def site(hook, code):
    return hook.call(lambda h: own(h, code))
"""),
    ),
    "an aliased copier": (
        "copier",
        "site",
        """
from aibi.core.schema.copiers import is_true as truth


def site(hook, code):
    return hook.call(lambda h: truth(h, code))
""",
    ),
    "a copier shadowed by a parameter": (
        "copier",
        "site",
        _site("""
def site(hook, code, is_true):
    return hook.call(lambda h: is_true(h, code))
"""),
    ),
    "a copier rebound in its module": (
        "copier",
        "site",
        _site("""
is_true = print


def site(hook, code):
    return hook.call(lambda h: is_true(h, code))
"""),
    ),
    "a copier module aliased": (
        "copier",
        "site",
        """
from aibi.core.schema import copiers as made


def site(hook, code):
    return hook.call(lambda h: made.is_true(h, code))
""",
    ),
    # --- allowance: closed over, bound by guards in the call's block, used once ---
    "an allowance made inside the run": (
        "allowance",
        "site",
        _site("""
def site(hook, inputs):
    return hook.call(
        lambda h: values(h.run, inputs, allowance=guards.allowance(1, 1, 1, NAMES), ends=None)
    )
"""),
    ),
    "an allowance hoisted out of a loop": (
        "allowance",
        "site",
        _site("""
def site(hooks, inputs):
    allowance = guards.allowance(1, 1, 1, ("a", "b", "c"))
    for hook in hooks:
        hook.call(lambda h: values(h.run, inputs, allowance=allowance, ends=None))
"""),
    ),
    "an allowance shared by two calls": (
        "allowance",
        "site",
        _site("""
def site(first, second, inputs):
    allowance = guards.allowance(1, 1, 1, ("a", "b", "c"))
    first.call(lambda h: values(h.run, inputs, allowance=allowance, ends=None))
    second.call(lambda h: values(h.run, inputs, allowance=allowance, ends=None))
"""),
    ),
    "an allowance not made by guards": (
        "allowance",
        "site",
        _site("""
def site(hook, inputs, make):
    allowance = make()
    return hook.call(lambda h: values(h.run, inputs, allowance=allowance, ends=None))
"""),
    ),
    "an allowance that is a module global": (
        "allowance",
        "site",
        _site("""
ALLOWANCE = guards.allowance(1, 1, 1, ("a", "b", "c"))


def site(hook, inputs):
    return hook.call(lambda h: values(h.run, inputs, allowance=ALLOWANCE, ends=None))
"""),
    ),
    # --- view: a compiler's or a caveat rule's view, by PackView.of in the call's block, once ---
    "a view memoised across calls": (
        "view",
        "site",
        _site("""
def site(hook, leaf, release, views, codes, form):
    view = views.setdefault(release.manifest, PackView.of(release))
    return hook.call(lambda h: caveat_codes(h, view, form, codes=codes))
"""),
    ),
    "a view shared by two compiles": (
        "view",
        "site",
        _site("""
def site(first, second, leaf, release):
    view = PackView.of(release)
    first.call(lambda h: expansion(h.compile, leaf, view, "1", pack="p"))
    return second.call(lambda h: expansion(h.compile, leaf, view, "1", pack="p"))
"""),
    ),
    "a view made outside the loop of its calls": (
        "view",
        "site",
        _site("""
def site(rules, release, codes, form):
    view = PackView.of(release)
    for rule in rules:
        rule.call(lambda h: caveat_codes(h, view, form, codes=codes))
"""),
    ),
    "PackView rebound in its module": (
        "view",
        "site",
        _site("""
from functools import cache

PackView = cache(PackView)
"""),
    ),
    "PackView.of aliased": (
        "view",
        "site",
        _site("""
def site(hook, leaf, release):
    of = PackView.of
    view = PackView.of(release) if of is None else of(release)
    return hook.call(lambda h: expansion(h.compile, leaf, view, "1", pack="p"))
"""),
    ),
    "PackView imported under another name": (
        "view",
        "site",
        """
from aibi.core.engine.resolve import PackView as View
""",
    ),
    "PackView.of replaced": (
        "view",
        "site",
        _site("""
PackView.of = staticmethod(lambda release: release)
"""),
    ),
    # --- slots ---
    "a handle's slot read": (
        "slots",
        "site",
        """
def site(hook):
    return hook._hook_object
""",
    ),
    "a closure's cell read": (
        "slots",
        "site",
        """
def site(run):
    return run.__closure__[0].cell_contents
""",
    ),
    # --- contain: only inside copiers.proposals ---
    "contain at a site": (
        "contain",
        "site",
        """
from aibi.core.schema.guards import contain


def site(read):
    return contain("p", "proposer", read)
""",
    ),
    "contain by attribute at a site": (
        "contain",
        "site",
        """
from aibi.core.schema import guards


def site(read):
    return guards.contain("p", "proposer", read)
""",
    ),
    "contain imported under another name": (
        "contain",
        "site",
        """
from aibi.core.schema.guards import contain as c


def site(read):
    return c("p", "proposer", read)
""",
    ),
    "contain held in proposals": (
        "contain",
        "copiers",
        """
from aibi.core.schema.guards import contain


def proposals(read):
    run = contain
    return run("p", "proposer", read)
""",
    ),
    "contain read by getattr": (
        "contain",
        "site",
        """
from aibi.core.schema import guards


def site(read):
    return getattr(guards, "contain")("p", "proposer", read)
""",
    ),
    # --- handler: a copier's handlers name exact core types ---
    "a broad handler in a copier": (
        "handler",
        "copiers",
        """
def copier(hook):
    try:
        return hook()
    except Exception:
        return None
""",
    ),
    "a handler's name rebound in its module": (
        "handler",
        "copiers",
        """
from pydantic import ValidationError

ValidationError = BaseException


def copier(hook):
    try:
        return hook()
    except ValidationError:
        return None
""",
    ),
    "a handler's name imported under another name": (
        "handler",
        "copiers",
        """
from pydantic import BaseModel as ValidationError


def copier(hook):
    try:
        return hook()
    except ValidationError:
        return None
""",
    ),
    "a handler's name rebound inside a copier": (
        "handler",
        "copiers",
        """
from pydantic import ValidationError


def copier(hook):
    ValidationError = BaseException
    try:
        return hook()
    except ValidationError:
        return None
""",
    ),
    "a handler's name from another module": (
        "handler",
        "copiers",
        """
from aibi.core.schema.refusals import ValidationError


def copier(hook):
    try:
        return hook()
    except ValidationError:
        return None
""",
    ),
    "a guard's handler name rebound": (
        "handler",
        "guards",
        """
BaseException = Exception


def contained(run):
    try:
        return run()
    except BaseException:
        return None
""",
    ),
    # --- state ---
    "a cached copier": (
        "state",
        "copiers",
        """
from functools import cache


@cache
def copier(hook):
    return hook()
""",
    ),
    "a copier that writes a global": (
        "state",
        "copiers",
        """
SEEN = 0


def copier(hook):
    global SEEN
    SEEN = 1
    return hook()
""",
    ),
    "a cache imported under another name": (
        "state",
        "copiers",
        """
from functools import lru_cache as memo


@memo
def copier(hook):
    return hook()
""",
    ),
    "a cache of an aliased functools": (
        "state",
        "guards",
        """
import functools as ft


@ft.cache
def guard(hook):
    return hook()
""",
    ),
    "a module-level dict written by subscript": (
        "state",
        "copiers",
        """
_SEEN = {}


def copier(hook):
    found = hook()
    _SEEN[id(found)] = found
    return found
""",
    ),
    "a module-level list appended to": (
        "state",
        "copiers",
        """
_KEPT = []


def copier(hook):
    found = hook()
    _KEPT.append(found)
    return found
""",
    ),
    "a module-level object written by attribute": (
        "state",
        "copiers",
        """
class _Box:
    last = None


_BOX = _Box()


def copier(hook):
    found = hook()
    _BOX.last = found
    return found
""",
    ),
    "a module-level object written by setattr": (
        "state",
        "copiers",
        """
class _Box:
    last = None


_BOX = _Box()


def copier(hook):
    found = hook()
    setattr(_BOX, "last", found)
    return found
""",
    ),
    "a guard's module-level set added to": (
        "state",
        "guards",
        """
_SEEN = set()


def guard(hook):
    found = hook()
    _SEEN.add(id(found))
    return found
""",
    ),
    # --- enter: only run.py's, into run_importer ---
    "enter at another site": (
        "enter",
        "site",
        """
def site(importer, source, options):
    return importer.enter(lambda h: h.import_source(source, options))
""",
    ),
}


__all__ = ["CASES"]

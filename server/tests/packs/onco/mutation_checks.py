"""The refusals of the oncology pack's importer, found in its source (M4.2b-1b, D444; review r2
MJ-A, r3 MJ-1).

This is not a test: pytest does not collect it, and it imports nothing of the pack at import, so
that a test module can load it by its path. It reads the modules of ``MODULES`` as source, never
as the pack's modules, and finds:

- every refusal **site**: a call of ``entry_refused``, ``placed`` or ``refused``, or of a helper
  that returns one (``refuse``, ``too_many``, ``refusal``, ``_value_refused``), whose reason it can
  read, and, in the two readers, an ``append`` of a ``(column, "<CODE>", reason)`` tuple and a
  ``return`` of a reason string (``missing``, ``_amino_acid``);
- each site's **templates**: its reason as written, each replacement field it cannot read written
  ``{}`` (a constant, an f-string, a module constant, a helper's argument, the values of a module
  mapping read with ``.get`` and its default). A site whose reason is a local name that only
  passes on a reason another site gave (``missing``, ``reason``) is not a site of its own;
- every **check**: an ``if`` whose own body (not a nested statement's) holds a site.

``dropped`` re-executes a module's source with one check's test made ``False`` and puts the
result's functions, classes and constants in place of the module's own, and of every other loaded
module's names bound to them, so that a test can run the corpus with that check gone.
"""

import ast
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FunctionType, ModuleType
from typing import Any

PACK = Path(__file__).resolve().parents[3] / "src" / "aibi" / "packs" / "onco"
READERS = ("mutation", "caselists")
"""The two readers of D444: their checks are held one by one (the inventory)."""
OTHERS = ("study", "meta", "importer")
"""Discovery, the meta files and the importer: their sites are D409's or D444's."""
MODULES = (*READERS, *OTHERS)
REFUSERS = frozenset({"entry_refused", "placed", "refused"})
"""The functions of ``refusals`` that make a refusal: the reason is the last argument."""
PRODUCERS = frozenset({"missing", "_amino_acid"})
"""The readers' functions whose returned strings are reasons (passed to a site by name)."""
FIELD = "{}"


@dataclass(frozen=True)
class Check:
    module: str
    line: int
    templates: tuple[str, ...]

    @property
    def id(self) -> str:
        return f"{self.module}:{self.line}"

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(pattern(template) for template in self.templates)

    def matches(self, reason: str) -> bool:
        return any(re.fullmatch(found, reason, re.DOTALL) for found in self.reasons)


def pattern(template: str) -> str:
    """The regular expression a template's reasons match whole."""
    return ".+".join(re.escape(part) for part in template.split(FIELD))


def _name(node: ast.expr | None) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


@dataclass(frozen=True)
class _Helper:
    params: tuple[str, ...]
    reason: ast.expr
    method: bool


class _Module:
    def __init__(self, module: str) -> None:
        self.module = module
        self.tree = ast.parse((PACK / f"{module}.py").read_text(encoding="utf-8"))
        self.constants: dict[str, ast.expr] = {}
        for statement in self.tree.body:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target = statement.targets[0]
                if isinstance(target, ast.Name):
                    self.constants[target.id] = statement.value
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                if statement.value is not None:
                    self.constants[statement.target.id] = statement.value
        self.helpers: dict[str, _Helper] = {}
        self.inside: set[int] = set()
        """The calls a helper returns (its own body's): no site of their own."""
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            params = tuple(arg.arg for arg in node.args.args)
            for inner in _own(node):
                if not (isinstance(inner, ast.Return) and isinstance(inner.value, ast.Call)):
                    continue
                name = _name(inner.value)
                if name in REFUSERS and inner.value.args:
                    reason = inner.value.args[-1]
                    if _uses(reason, params):
                        method = bool(params) and params[0] == "self"
                        self.helpers[node.name] = _Helper(params, reason, method)
                        self.inside.add(id(inner.value))
        # a helper returning another helper's call with a constant reason (``too_many``)
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.FunctionDef) or node.name in self.helpers:
                continue
            body = [
                statement
                for statement in node.body
                if not (
                    isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
                )
            ]
            only = body[0] if len(body) == 1 else None
            if (
                isinstance(only, ast.Return)
                and isinstance(only.value, ast.Call)
                and _name(only.value) in self.helpers
            ):
                params = tuple(arg.arg for arg in node.args.args)
                self.helpers[node.name] = _Helper(params, only.value, bool(params))
                self.inside.add(id(only.value))

    def render(self, node: ast.expr, env: Mapping[str, str | None]) -> list[str] | None:
        """``node``'s templates, ``env`` the parameters bound; ``None`` when it is a local name
        that passes a reason on."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.JoinedStr):
            parts: list[list[str]] = [[""]]
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append([value.value])
                elif isinstance(value, ast.FormattedValue):
                    inner = value.value
                    bound = env.get(inner.id) if isinstance(inner, ast.Name) else None
                    parts.append([bound] if bound is not None else [FIELD])
            found = [""]
            for options in parts:
                found = [left + right for left in found for right in options]
            return found
        if isinstance(node, ast.Name):
            if node.id in env:
                bound = env[node.id]
                return [bound] if bound is not None else None
            if node.id in self.constants:
                return self.render(self.constants[node.id], env)
            return None
        if isinstance(node, ast.Call):
            name = _name(node)
            if name == "text" and node.args:
                return self.render(node.args[0], env)
            if (
                name == "get"
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in self.constants
            ):
                mapping = self.constants[node.func.value.id]
                if isinstance(mapping, ast.Call) and mapping.args:
                    mapping = mapping.args[0]
                found: list[str] = []
                if isinstance(mapping, ast.Dict):
                    for value in mapping.values:
                        found.extend(self.render(value, env) or ())
                for default in node.args[1:]:
                    found.extend(self.render(default, env) or ())
                return found or None
            if name in self.helpers:
                return self.site(node, env)
        return None

    def site(self, call: ast.Call, env: Mapping[str, str | None]) -> list[str] | None:
        """The templates of a refusal call, or ``None``."""
        name = _name(call)
        if name in REFUSERS and call.args:
            return self.render(call.args[-1], env)
        helper = self.helpers.get(name or "")
        if helper is None:
            return None
        params = helper.params[1:] if helper.method else helper.params
        bound: dict[str, str | None] = {}
        for param, argument in zip(params, call.args, strict=False):
            found = self.render(argument, env)
            bound[param] = found[0] if found and len(found) == 1 else None
        found = self.render(helper.reason, bound)
        if found is not None:
            found = [template for template in found if template]
        return found or None

    def statement(self, statement: ast.stmt) -> list[str]:
        """The templates of the sites a statement holds itself (not in a nested block)."""
        value: ast.expr | None = None
        if isinstance(statement, ast.Raise):
            value = statement.exc
        elif isinstance(statement, ast.Assign | ast.Return | ast.Expr):
            value = statement.value
        if value is None:
            return []
        if (
            isinstance(statement, ast.Return)
            and self.module in READERS
            and (
                isinstance(value, ast.JoinedStr)
                or (isinstance(value, ast.Constant) and isinstance(value.value, str))
            )
        ):
            return self.render(value, {}) or []
        if isinstance(value, ast.Call) and id(value) not in self.inside:
            if _name(value) == "append" and value.args:
                found = value.args[0]
                if isinstance(found, ast.Tuple) and len(found.elts) == 3:
                    return self.render(found.elts[2], {}) or []
                return []
            return self.site(value, {}) or []
        return []

    def in_producer(self) -> set[int]:
        found: set[int] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name in PRODUCERS:
                found.update(id(inner) for inner in ast.walk(node))
        return found


def _own(function: ast.FunctionDef) -> Iterator[ast.AST]:
    """The nodes of ``function``'s own body, not of a function nested in it."""
    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, ast.FunctionDef):
            stack.extend(ast.iter_child_nodes(node))


def _uses(node: ast.expr, params: Sequence[str]) -> bool:
    return any(isinstance(inner, ast.Name) and inner.id in params for inner in ast.walk(node))


def _blocks(node: ast.AST) -> Iterator[list[ast.stmt]]:
    for field in ("body", "orelse", "finalbody"):
        block = getattr(node, field, None)
        if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
            yield block
    for handler in getattr(node, "handlers", ()):
        yield handler.body


def sites(module: str) -> list[tuple[int, tuple[str, ...]]]:
    """Every refusal site of ``module``: its line and its templates."""
    walked = _Module(module)
    producers = walked.in_producer()
    found: list[tuple[int, tuple[str, ...]]] = []
    seen: set[int] = set()
    for node in ast.walk(walked.tree):
        for block in _blocks(node):
            for statement in block:
                if id(statement) in seen:
                    continue
                seen.add(id(statement))
                templates = walked.statement(statement)
                returns = isinstance(statement, ast.Return)
                if returns and id(statement) not in producers and _name(statement.value) is None:
                    templates = []
                if templates:
                    found.append((statement.lineno, tuple(dict.fromkeys(templates))))
    return sorted(found)


def templates(module: str) -> set[str]:
    return {template for _, found in sites(module) for template in found}


def checks(module: str) -> list[Check]:
    """Every check of ``module``, by line."""
    walked = _Module(module)
    producers = walked.in_producer()
    found: list[Check] = []
    for node in ast.walk(walked.tree):
        if not isinstance(node, ast.If):
            continue
        reasons: list[str] = []
        for statement in node.body:
            templates_ = walked.statement(statement)
            returns = isinstance(statement, ast.Return)
            if returns and id(statement) not in producers and _name(statement.value) is None:
                templates_ = []
            reasons.extend(templates_)
        if reasons:
            found.append(Check(module, node.lineno, tuple(dict.fromkeys(reasons))))
    return sorted(found, key=lambda check: (check.module, check.line))


def every_check(modules: Sequence[str] = READERS) -> list[Check]:
    return [check for module in modules for check in checks(module)]


class _Dropped(ast.NodeTransformer):
    def __init__(self, line: int) -> None:
        self.line = line

    def visit_If(self, node: ast.If) -> ast.AST:
        self.generic_visit(node)
        if node.lineno == self.line:
            node.test = ast.Constant(False)
        return node


@contextmanager
def dropped(check: Check, pack: Mapping[str, ModuleType]) -> Iterator[None]:
    """The module ``check.module`` with ``check``'s test made ``False``: its functions, classes
    and constants put in place of the loaded module's, and of every other module's names bound to
    the same objects, for the duration, then restored."""
    module = pack[check.module]
    tree = _Dropped(check.line).visit(ast.parse((PACK / f"{check.module}.py").read_text()))
    ast.fix_missing_locations(tree)
    namespace: dict[str, Any] = {"__name__": module.__name__}
    exec(compile(tree, str(PACK / f"{check.module}.py"), "exec"), namespace)
    saved = {name: dict(vars(found)) for name, found in pack.items()}
    old = {id(value): name for name, value in saved[check.module].items()}
    # an instance made before (the pack's ``OncoImporter``) keeps its class: the class's own
    # functions are swapped in place, so that its methods run the dropped code too
    methods: list[tuple[type, str, Any]] = []
    for name, value in namespace.items():
        before = saved[check.module].get(name)
        if isinstance(value, type) and isinstance(before, type) and before is not value:
            for attribute, function in vars(value).items():
                if isinstance(function, FunctionType):
                    methods.append((before, attribute, vars(before).get(attribute)))
                    setattr(before, attribute, function)
    try:
        for name, value in namespace.items():
            if not name.startswith("__"):
                setattr(module, name, value)
        for other, found in pack.items():
            if other == check.module:
                continue
            for name, value in saved[other].items():
                original = old.get(id(value))
                if original is not None and original in namespace and callable(value):
                    setattr(found, name, namespace[original])
        yield
    finally:
        for owner, attribute, function in reversed(methods):
            if function is None:
                delattr(owner, attribute)
            else:
                setattr(owner, attribute, function)
        for name, found in pack.items():
            for attribute in list(vars(found)):
                if attribute not in saved[name]:
                    delattr(found, attribute)
            for attribute, value in saved[name].items():
                setattr(found, attribute, value)


REASON_PREFIX = re.compile(r"^ (?:line \d+(?:, column \d+)?|column \d+): | ")


def reason_of(message: Any) -> str:
    """The pack's reason in a refusal's message: its last text segment, past the place
    (``placed``) or the space after an entry's name (``entry_refused``)."""
    texts = [segment.text for segment in message if hasattr(segment, "text")]
    return REASON_PREFIX.sub("", texts[-1], count=1) if texts else ""


__all__ = [
    "MODULES",
    "OTHERS",
    "READERS",
    "Check",
    "checks",
    "dropped",
    "every_check",
    "pattern",
    "reason_of",
    "sites",
    "templates",
]

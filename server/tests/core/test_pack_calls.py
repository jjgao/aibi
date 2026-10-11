"""Every call of a pack's code goes through one guard, in one shape (SPEC §10.1, D403).

The core reaches a hook's code only as ``<handle>.call(<run>)`` (or, for the importer,
``<handle>.enter(<run>)`` into ``run_importer``'s own guard, D400), where ``run`` is a lambda, or a
nested ``def`` whose body is one ``return``, that is one copier's call: ``COPIER(h.METHOD, ...)``
or ``COPIER(h, ...)``. This test reads the core's source and holds every such call to that shape:

- ``call``: ``.call`` is only ever called at once, with exactly one argument, a run; nothing in
  the core refers to ``operator.methodcaller`` or ``attrgetter`` at all (imported, aliased, held,
  read as an attribute or named as text), and no ``getattr`` reads a name that is not a literal off
  a value bound from a hook source (a parameter annotated as a ``Hook``, or a registry's handle);
- ``run``: a run has one parameter, no defaults, and its body is a copier's call;
- ``hook``: the parameter appears once, as the copier's first argument, alone or as ``h.METHOD``;
- ``argument``: every other argument of the copier is a name or a literal;
- ``copier``: the copier is one of ``copiers.COPIERS``, imported from ``schema.copiers`` unaliased
  (or reached as ``copiers.X``), global in the run's scope (``symtable``) and bound once in its
  module;
- ``allowance``: an ``allowance=`` or ``allowances=`` argument is a name the run closes over,
  bound once by a direct call of ``guards.allowance`` or ``guards.allowances`` in the same block
  as the call, with no loop or comprehension between them, and used once;
- ``view``: the view a leaf compiler or a caveat rule is given is a name bound once by a direct
  ``PackView.of(...)`` in the same block, and used once (D285, D287); ``PackView`` is bound once
  in its module, by its class or an unaliased import, and neither it nor ``PackView.of`` is
  rebound, aliased or replaced;
- ``getattribute``: nothing outside ``schema/guards.py`` refers to ``__getattribute__``;
- ``slots``: nothing outside ``schema/guards.py`` names a handle's slots, a closure or a cell;
- ``contain``: ``contain`` is called only inside ``copiers.proposals``, by its own name imported
  there unaliased: it is never imported elsewhere or under another name, read as an attribute
  (``guards.contain``), held, rebound or named as text to ``getattr``;
- ``handler``: a copier's handlers name only exact core types (no ``Exception``, no bare
  ``except``), each imported once, unaliased, from its own module; and no name a handler of
  ``copiers`` or ``guards`` names is bound anywhere else in its module (assigned, a parameter, an
  ``except … as``, a nested ``def`` or ``class``, imported under another name or twice);
- ``state``: neither ``guards`` nor ``copiers`` imports or uses ``functools``' caches (``cache``,
  ``lru_cache``, ``cached_property``, aliased or not, or ``*``), writes a global (``global``,
  ``nonlocal``), or, inside a function, writes a module-level binding (a subscript or attribute
  store or ``del``, a call of a mutating method such as ``append``, ``update`` or ``add``, or
  ``setattr`` and the like on it);
- ``enter``: ``.enter`` is only ``importers/run.py``'s, into ``run_importer``.

A fixture module (``pack_call_violations``) holds one violation per rule, each a test of its own, so
that a checker which finds nothing fails.

This is a **tripwire over the core's own code, not a proof** (D403): it reads the source as
written, so it cannot see dynamic dispatch the core does not use (a name computed at run time
and looked up through ``globals()``, ``vars()``, ``eval`` or a mapping of the core's functions,
an attribute reached through ``type(x).__dict__``, a descriptor or a metaclass), nor what a
value holds at run time (a leaf or a form shared between calls). The dynamic check
(``catalog/test_pack_guards``), the twins and the provenance tests hold the behaviour.
"""

import ast
import symtable
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from tests.core.pack_call_violations import CASES

import aibi.core
from aibi.core.schema import copiers

CORE = Path(aibi.core.__file__).parent
_COPIERS_SOURCE = (CORE / "schema" / "copiers.py").read_text()

_SLOTS = ("_hook_object", "_hook_pack", "_hook_stage", "__closure__", "cell_contents")
_HANDLERS: dict[str, str] = {
    "JsonTooLarge": "aibi.core.schema.guards",
    "_NotJsonError": "aibi.core.schema.guards",
    "_Foreign": "aibi.core.schema.guards",
    "Refused": "aibi.core.schema.pack_api",
    "JsonError": "aibi.core.schema.jsonio",
    "ValidationError": "pydantic",
    "PydanticSerializationError": "pydantic_core",
}
"""The exact core types (and pydantic's) a copier's handlers may name, each with the module it is
imported from."""
_CACHES = ("cache", "lru_cache", "cached_property")
"""``functools``' caches, which neither ``copiers`` nor ``guards`` uses."""
_MUTATORS = frozenset(
    {
        "append",
        "extend",
        "insert",
        "update",
        "add",
        "setdefault",
        "pop",
        "popitem",
        "clear",
        "remove",
        "discard",
        "sort",
        "reverse",
        "__setitem__",
        "__delitem__",
        "__setattr__",
        "__delattr__",
        "__ior__",
        "__iadd__",
    }
)
"""The methods of the built-in containers (and the dunders) that change what they are called on."""
_WRITERS = frozenset({"setattr", "delattr", "__setattr__", "__delattr__", "__setitem__"})
"""The functions that write what their first argument is."""
_LOOPS = (
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Lambda,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
)
_HOOK_GETTERS = frozenset(
    {
        "importer",
        "validators",
        "proposers",
        "facets",
        "caveat_rules",
        "leaf_kind",
        "leaf_summary",
        "translator",
        "requirement_predicate",
        "ontology_validator",
        "implementation",
    }
)
"""The registry's members that give a handle (D403), and a registered analysis's."""
_ATTRIBUTE_READS = ("getattr", "setattr", "hasattr", "delattr")
_PER_CALL = {"expansion": 2, "caveat_codes": 1}
"""The copiers whose hooks get a view of their own per call, and the position of the view among
the copier's arguments."""


def _copier_names() -> frozenset[str]:
    """The names in ``COPIERS``, read from its literal tuple."""
    for node in ast.parse(_COPIERS_SOURCE).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "COPIERS"
        ):
            assert isinstance(node.value, ast.Tuple), "COPIERS is a literal tuple"
            names = [item.id for item in node.value.elts if isinstance(item, ast.Name)]
            assert len(names) == len(node.value.elts)
            return frozenset(names)
    raise AssertionError("copiers.py defines no COPIERS")


COPIER_NAMES = _copier_names()
_Function = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
Binding = Callable[[ast.expr], bool]
"""Whether an assignment's value is one a rule takes."""


@dataclass(frozen=True)
class Violation:
    rule: str
    line: int


class _Checked:
    """The checks of one module's source (module docstring)."""

    def __init__(self, source: str, role: str) -> None:
        self.source = source
        self.role = role
        self.tree = ast.parse(source)
        self.table = symtable.symtable(source, "<module>", "exec")
        self.parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
        self.found: list[Violation] = []

    def flag(self, rule: str, node: ast.AST) -> None:
        self.found.append(Violation(rule, getattr(node, "lineno", 0)))

    # --- Name resolution ---

    def _module_bindings(self, name: str) -> list[ast.AST]:
        """Every binding of ``name`` at module level: an import, a ``def`` or ``class``, an
        assignment at the top of the module, or a ``global`` declaration of it anywhere."""
        found: list[ast.AST] = []
        for node in self.tree.body:
            if isinstance(node, ast.ImportFrom | ast.Import):
                found += [alias for alias in node.names if (alias.asname or alias.name) == name]
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                if node.name == name:
                    found.append(node)
            else:
                found += [
                    one
                    for one in ast.walk(node)
                    if isinstance(one, ast.Name)
                    and isinstance(one.ctx, ast.Store)
                    and one.id == name
                ]
        found += [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Global | ast.Nonlocal) and name in node.names
        ]
        return found

    def _imported_from(self, name: str, module: str) -> bool:
        """Whether ``name`` is bound once in the module, by ``from <module> import <name>``,
        unaliased."""
        bindings = self._module_bindings(name)
        if len(bindings) != 1 or not isinstance(bindings[0], ast.alias):
            return False
        alias = bindings[0]
        parent = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ImportFrom) and alias in node.names
        )
        return parent.module == module and alias.asname is None

    def _scope(self, node: ast.Lambda | ast.FunctionDef) -> symtable.SymbolTable | None:
        """The symbol table of a lambda or a nested ``def``, found by its line and name."""
        name = "lambda" if isinstance(node, ast.Lambda) else node.name
        pending = [self.table]
        found: list[symtable.SymbolTable] = []
        while pending:
            table = pending.pop()
            if table.get_name() == name and table.get_lineno() == node.lineno:
                found.append(table)
            pending.extend(table.get_children())
        return found[0] if len(found) == 1 else None

    # --- Functions and blocks ---

    def _function(self, node: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        current = self.parents.get(node)
        while current is not None and not isinstance(
            current, ast.FunctionDef | ast.AsyncFunctionDef
        ):
            current = self.parents.get(current)
        return current

    @staticmethod
    def _blocks(node: ast.AST) -> Iterator[list[ast.stmt]]:
        for name in ("body", "orelse", "finalbody"):
            block = getattr(node, name, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                yield block  # pyright: ignore[reportUnknownArgumentType]
        for handler in getattr(node, "handlers", []):
            yield handler.body

    def _bound_in_block(self, call: ast.Call, name: str, bound: Binding) -> bool:
        """Whether ``name`` is bound once in the call's function, by an assignment ``bound``
        accepts, in a block that holds a statement the call is in, before it, with no loop,
        comprehension or function between that statement and the call; and used once there."""
        function = self._function(call)
        if function is None:
            return False
        assignments = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
        ]
        stores = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Store)
        ]
        loads = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load)
        ]
        if len(assignments) != 1 or len(stores) != 1 or len(loads) != 1:
            return False
        [assignment] = assignments
        if len(assignment.targets) != 1 or not bound(assignment.value):
            return False
        chain: list[ast.AST] = []
        current: ast.AST | None = call
        while current is not None and current is not function:
            chain.append(current)
            current = self.parents.get(current)
        for block in [b for node in ast.walk(function) for b in self._blocks(node)]:
            if assignment not in block:
                continue
            at = block.index(assignment)
            for position, node in enumerate(chain):
                if isinstance(node, ast.stmt) and node in block and block.index(node) > at:
                    return not any(isinstance(n, _LOOPS) for n in chain[1 : position + 1])
        return False

    # --- The rules ---

    def check(self) -> list[Violation]:
        self._pack_view_bindings()
        guarded = self.role in ("copiers", "guards")
        for node in ast.walk(self.tree):
            if self.role != "guards" and _names_getattribute(node):
                self.flag("getattribute", node)
            if _names_operator_getter(node):
                self.flag("call", node)
            if self.role != "guards":
                self._contain_named(node)
            if guarded and _names_cache(node):
                self.flag("state", node)
            if isinstance(node, ast.Attribute):
                self._attribute(node)
                self._pack_view_of_use(node)
            elif isinstance(node, ast.Name) and node.id == "PackView":
                self._pack_view_use(node)
            elif isinstance(node, ast.Call):
                self._getattr(node)
            elif isinstance(node, ast.ExceptHandler) and self.role == "copiers":
                self._handler(node)
            elif isinstance(node, ast.Global | ast.Nonlocal) and guarded:
                self.flag("state", node)
            elif isinstance(node, ast.FunctionDef) and guarded:
                for decorator in node.decorator_list:
                    named = decorator.func if isinstance(decorator, ast.Call) else decorator
                    text = ast.unparse(named)
                    if text.split(".")[-1] in _CACHES:
                        self.flag("state", decorator)
        if guarded:
            self._module_writes()
            self._handler_bindings()
        return self.found

    def _attribute(self, node: ast.Attribute) -> None:
        if node.attr in _SLOTS and self.role != "guards":
            self.flag("slots", node)
        if node.attr not in ("call", "enter") or self.role == "guards":
            return
        parent = self.parents.get(node)
        if not isinstance(parent, ast.Call) or parent.func is not node:
            self.flag("call", node)
            return
        if len(parent.args) != 1 or parent.keywords or isinstance(parent.args[0], ast.Starred):
            self.flag("call", node)
            return
        run = self._run(parent.args[0], parent)
        if run is None:
            self.flag("run", node)
            return
        if node.attr == "enter":
            self._enter(parent, run)
        else:
            self._copier_call(parent, run)

    def _run(self, given: ast.expr, call: ast.Call) -> ast.Lambda | ast.FunctionDef | None:
        """The run a call is given: a lambda, or the nested ``def`` its name binds in the
        call's function, of one parameter without defaults."""
        if isinstance(given, ast.Lambda):
            found: ast.Lambda | ast.FunctionDef = given
        elif isinstance(given, ast.Name):
            function = self._function(call)
            nested = (
                []
                if function is None
                else [
                    node
                    for node in ast.walk(function)
                    if isinstance(node, ast.FunctionDef) and node.name == given.id
                ]
            )
            if len(nested) != 1 or nested[0].decorator_list:
                return None
            found = nested[0]
            if len(found.body) != 1 or not isinstance(found.body[0], ast.Return):
                return None
        else:
            return None
        arguments = found.args
        if (
            len(arguments.args) != 1
            or arguments.posonlyargs
            or arguments.kwonlyargs
            or arguments.vararg
            or arguments.kwarg
            or arguments.defaults
            or arguments.kw_defaults
        ):
            return None
        return found

    @staticmethod
    def _body(run: ast.Lambda | ast.FunctionDef) -> ast.expr | None:
        if isinstance(run, ast.Lambda):
            return run.body
        returned = run.body[0]
        assert isinstance(returned, ast.Return)
        return returned.value

    def _copier_call(self, call: ast.Call, run: ast.Lambda | ast.FunctionDef) -> None:
        body = self._body(run)
        if not isinstance(body, ast.Call):
            self.flag("run", run)
            return
        parameter = run.args.args[0].arg
        uses = [n for n in ast.walk(body) if isinstance(n, ast.Name) and n.id == parameter]
        first = body.args[0] if body.args else None
        if (
            len(uses) != 1
            or first is None
            or not (
                (isinstance(first, ast.Name) and first.id == parameter)
                or (
                    isinstance(first, ast.Attribute)
                    and isinstance(first.value, ast.Name)
                    and first.value.id == parameter
                )
            )
        ):
            self.flag("hook", run)
            return
        name = self._copier(body.func, run)
        if name is None:
            self.flag("copier", run)
            return
        scope = self._scope(run)
        for keyword in body.keywords:
            if keyword.arg not in ("allowance", "allowances"):
                continue
            value = keyword.value
            closed = (
                isinstance(value, ast.Name)
                and scope is not None
                and scope.lookup(value.id).is_free()
            )
            if not closed or not self._bound_in_block(
                call, cast(ast.Name, value).id, _from_guards(cast(str, keyword.arg))
            ):
                self.flag("allowance", keyword)
                return
        for argument in [*body.args[1:], *(k.value for k in body.keywords)]:
            if not isinstance(argument, ast.Name | ast.Constant):
                self.flag("argument", argument)
                return
        if any(keyword.arg is None for keyword in body.keywords):
            self.flag("argument", body)
            return
        if name in _PER_CALL:
            at = _PER_CALL[name]
            given = body.args[at] if len(body.args) > at else None
            if not isinstance(given, ast.Name) or not self._bound_in_block(
                call, given.id, _pack_view_of
            ):
                self.flag("view", body)

    def _copier(self, func: ast.expr, run: ast.Lambda | ast.FunctionDef) -> str | None:
        """The copier a run calls, if it resolves to one of ``COPIERS``."""
        scope = self._scope(run)
        if scope is None:
            return None
        if isinstance(func, ast.Name):
            symbol = scope.lookup(func.id)
            if not symbol.is_global() or symbol.is_local() or symbol.is_free():
                return None
            if func.id not in COPIER_NAMES:
                return None
            if self.role == "copiers":
                return func.id if len(self._module_bindings(func.id)) == 1 else None
            return func.id if self._imported_from(func.id, "aibi.core.schema.copiers") else None
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.attr in COPIER_NAMES
        ):
            symbol = scope.lookup(func.value.id)
            if symbol.is_global() and self._imported_from(func.value.id, "aibi.core.schema"):
                return func.attr if func.value.id == "copiers" else None
        return None

    def _enter(self, call: ast.Call, run: ast.Lambda | ast.FunctionDef) -> None:
        body = self._body(run)
        parameter = run.args.args[0].arg
        uses = [n for n in ast.walk(run) if isinstance(n, ast.Name) and n.id == parameter]
        if (
            self.role != "importer-run"
            or not isinstance(body, ast.Call)
            or not isinstance(body.func, ast.Name)
            or body.func.id != "run_importer"
            or not body.args
            or not isinstance(body.args[0], ast.Name)
            or body.args[0].id != parameter
            or len(uses) != 1
        ):
            self.flag("enter", call)

    def _getattr(self, node: ast.Call) -> None:
        if not (
            isinstance(node.func, ast.Name)
            and node.func.id in _ATTRIBUTE_READS
            and len(node.args) >= 2
        ):
            return
        named = node.args[1]
        if isinstance(named, ast.Constant):
            if named.value in ("call", "enter", *_SLOTS):
                self.flag("call", node)
            if named.value == "contain" and self.role != "guards":
                self.flag("contain", node)
            return
        if self._from_hook(node.args[0], self._hook_sources(self._function(node))):
            self.flag("call", node)

    def _hook_sources(self, function: ast.AST | None) -> frozenset[str]:
        """The names a function binds from a hook source: a parameter whose annotation names a
        ``Hook``, or a name assigned (or iterated) from an expression that reads a registry's
        handle or another such name, to a fixed point."""
        if function is None:
            return frozenset()
        found: set[str] = set()
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            arguments = function.args
            for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
                annotation = argument.annotation
                if annotation is not None and "Hook" in ast.unparse(annotation):
                    found.add(argument.arg)
        pairs: list[tuple[ast.expr, ast.expr]] = []
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                pairs += [(target, node.value) for target in node.targets]
            elif isinstance(node, ast.AnnAssign | ast.NamedExpr) and node.value is not None:
                pairs.append((node.target, node.value))
            elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
                pairs.append((node.target, node.iter))
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                pairs.append((node.optional_vars, node.context_expr))
        changed = True
        while changed:
            changed = False
            for target, value in pairs:
                if not self._from_hook(value, found):
                    continue
                for name in ast.walk(target):
                    if isinstance(name, ast.Name) and name.id not in found:
                        found.add(name.id)
                        changed = True
        return frozenset(found)

    @staticmethod
    def _from_hook(value: ast.AST, sources: set[str] | frozenset[str]) -> bool:
        """Whether an expression reads a registry's handle, or a name bound from one."""
        for node in ast.walk(value):
            if isinstance(node, ast.Name) and node.id in sources:
                return True
            if isinstance(node, ast.Attribute) and node.attr in _HOOK_GETTERS:
                return True
        return False

    def _pack_view_bindings(self) -> None:
        """``PackView`` bound once in its module, by its class or by an unaliased import from
        ``engine.resolve``, and never bound under another name."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom | ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if (alias.name == "PackView") != (bound == "PackView"):
                        self.flag("view", node)
            elif (
                isinstance(node, ast.Name)
                and node.id == "PackView"
                and isinstance(node.ctx, ast.Store | ast.Del)
            ) or (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and node.name == "PackView"
                and node not in self.tree.body
            ):
                self.flag("view", node)
        bindings = self._module_bindings("PackView")
        if len(bindings) > 1:
            self.flag("view", bindings[1])
        elif (
            bindings
            and isinstance(bindings[0], ast.alias)
            and not self._imported_from("PackView", "aibi.core.engine.resolve")
        ):
            self.flag("view", bindings[0])

    def _pack_view_use(self, node: ast.Name) -> None:
        """A load of ``PackView`` that hands the class on rather than using it: anything but its
        attribute, its call, an annotation or ``isinstance``'s class."""
        if not isinstance(node.ctx, ast.Load):
            return
        child: ast.AST = node
        parent = self.parents.get(node)
        while isinstance(parent, ast.Tuple | ast.BinOp | ast.Subscript):
            child, parent = parent, self.parents.get(parent)
        if isinstance(parent, ast.Attribute) and parent.value is child:
            return
        if isinstance(parent, ast.Call) and (
            parent.func is child
            or (
                isinstance(parent.func, ast.Name)
                and parent.func.id in ("isinstance", "issubclass")
                and child in parent.args[1:]
            )
        ):
            return
        if isinstance(parent, ast.arg | ast.AnnAssign) and parent.annotation is child:
            return
        if isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef) and parent.returns is child:
            return
        if isinstance(parent, ast.Compare):
            return
        self.flag("view", node)

    def _pack_view_of_use(self, node: ast.Attribute) -> None:
        """``PackView.of`` only ever called at once, never read as a value or replaced."""
        if not (
            node.attr == "of" and isinstance(node.value, ast.Name) and node.value.id == "PackView"
        ):
            return
        parent = self.parents.get(node)
        if not isinstance(node.ctx, ast.Load) or not (
            isinstance(parent, ast.Call) and parent.func is node
        ):
            self.flag("view", node)

    def _contain_named(self, node: ast.AST) -> None:
        """``contain`` reached otherwise than by a call of its own name inside
        ``copiers.proposals``, where it is imported unaliased: an import elsewhere or under
        another name, an attribute, a load that is not that call, or a binding."""
        if isinstance(node, ast.alias):
            named = "contain" in (node.name.split(".")[-1], node.asname or node.name)
            if named and (self.role != "copiers" or node.asname is not None):
                self.flag("contain", node)
        elif isinstance(node, ast.Attribute) and node.attr == "contain":
            self.flag("contain", node)
        elif isinstance(node, ast.Name) and node.id == "contain":
            parent = self.parents.get(node)
            function = self._function(node)
            if (
                not isinstance(node.ctx, ast.Load)
                or self.role != "copiers"
                or not isinstance(parent, ast.Call)
                or parent.func is not node
                or function is None
                or function.name != "proposals"
                or not self._imported_from("contain", "aibi.core.schema.guards")
            ):
                self.flag("contain", node)
        elif (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and node.name == "contain"
        ) or (isinstance(node, ast.arg) and node.arg == "contain"):
            self.flag("contain", node)

    def _innermost(self, node: ast.AST) -> _Function | None:
        current = self.parents.get(node)
        while current is not None and not isinstance(
            current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
        ):
            current = self.parents.get(current)
        return current

    def _global_root(self, value: ast.expr) -> bool:
        """Whether an expression is, or reads through attributes and subscripts, a module-level
        binding, as the scope it is in sees it."""
        root = value
        while isinstance(root, ast.Attribute | ast.Subscript):
            root = root.value
        if not isinstance(root, ast.Name) or not self._module_bindings(root.id):
            return False
        function = self._innermost(value)
        if function is None:
            return False  # written while the module is imported
        scope = self._scope(cast(ast.Lambda | ast.FunctionDef, function))
        if scope is None:
            return True
        try:
            symbol = scope.lookup(root.id)
        except KeyError:
            return True
        return symbol.is_global() and not symbol.is_local()

    def _module_writes(self) -> None:
        """A function's write of a module-level binding: a subscript or attribute store or
        ``del`` through it, a call of a mutating method on it, or ``setattr`` and the like."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Subscript | ast.Attribute) and isinstance(
                node.ctx, ast.Store | ast.Del
            ):
                if self._global_root(node.value):
                    self.flag("state", node)
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in _MUTATORS:
                    if self._global_root(func.value):
                        self.flag("state", node)
                elif (
                    ast.unparse(func).split(".")[-1] in _WRITERS
                    and node.args
                    and self._global_root(node.args[0])
                ):
                    self.flag("state", node)

    def _handler_bindings(self) -> None:
        """The names a handler of the module names, bound only once at module level, by an
        unaliased import or a ``class``, and nowhere else."""
        names = {
            one.id
            for handler in ast.walk(self.tree)
            if isinstance(handler, ast.ExceptHandler) and handler.type is not None
            for one in ast.walk(handler.type)
            if isinstance(one, ast.Name)
        }
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
                bound = node.id
            elif isinstance(node, ast.arg):
                bound = node.arg
            elif isinstance(node, ast.ExceptHandler | ast.FunctionDef | ast.AsyncFunctionDef):
                bound = node.name
            elif isinstance(node, ast.ClassDef):
                bound = None if node in self.tree.body else node.name
            elif isinstance(node, ast.alias):
                given = node.asname or node.name
                bound = given if given != node.name.split(".")[-1] else None
            elif isinstance(node, ast.Global | ast.Nonlocal):
                bound = next((name for name in node.names if name in names), None)
            else:
                continue
            if bound is not None and bound in names:
                self.flag("handler", node)
        for name in sorted(names):
            if len(self._module_bindings(name)) > 1:
                self.flag("handler", self.tree)

    def _handler(self, node: ast.ExceptHandler) -> None:
        kinds = node.type
        named = (
            [kinds]
            if isinstance(kinds, ast.Name)
            else list(kinds.elts)
            if isinstance(kinds, ast.Tuple)
            else []
        )
        if (
            kinds is None
            or not named
            or not all(
                isinstance(one, ast.Name)
                and one.id in _HANDLERS
                and self._imported_from(one.id, _HANDLERS[one.id])
                for one in named
            )
        ):
            self.flag("handler", node)


def _names_operator_getter(node: ast.AST) -> bool:
    """Whether a node refers to ``operator.methodcaller`` or ``attrgetter``: imported, as an
    attribute, a name or a literal."""
    getters = ("methodcaller", "attrgetter")
    return (
        (isinstance(node, ast.alias) and node.name.split(".")[-1] in getters)
        or (isinstance(node, ast.Attribute) and node.attr in getters)
        or (isinstance(node, ast.Name) and node.id in getters)
        or (isinstance(node, ast.Constant) and node.value in getters)
    )


def _names_cache(node: ast.AST) -> bool:
    """Whether a node imports or reads one of ``functools``' caches, or imports ``*``."""
    if isinstance(node, ast.ImportFrom):
        return node.module == "functools" and any(
            alias.name in (*_CACHES, "*") for alias in node.names
        )
    return isinstance(node, ast.Attribute) and node.attr in _CACHES


def _names_getattribute(node: ast.AST) -> bool:
    """Whether a node refers to ``__getattribute__``: as an attribute, a name or a literal."""
    return (
        (isinstance(node, ast.Attribute) and node.attr == "__getattribute__")
        or (isinstance(node, ast.Name) and node.id == "__getattribute__")
        or (isinstance(node, ast.Constant) and node.value == "__getattribute__")
    )


def _from_guards(keyword: str) -> Binding:
    """An assignment's value that is a direct call of ``guards.<keyword>`` (or of ``<keyword>``
    itself)."""

    def bound(value: ast.expr) -> bool:
        if not isinstance(value, ast.Call):
            return False
        func = value.func
        return (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "guards"
            and func.attr == keyword
        ) or (isinstance(func, ast.Name) and func.id == keyword)

    return bound


def _pack_view_of(value: ast.expr) -> bool:
    """An assignment's value that is a direct call of ``PackView.of``."""
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and isinstance(value.func.value, ast.Name)
        and value.func.value.id == "PackView"
        and value.func.attr == "of"
    )


def violations(source: str, role: str = "site") -> list[Violation]:
    return _Checked(source, role).check()


def _role(path: Path) -> str:
    relative = path.relative_to(CORE).as_posix()
    return {
        "schema/copiers.py": "copiers",
        "schema/guards.py": "guards",
        "importers/run.py": "importer-run",
    }.get(relative, "site")


def _core_modules() -> list[Path]:
    return sorted(CORE.rglob("*.py"))


def test_every_call_of_a_pack_s_code_in_the_core_has_the_one_shape() -> None:
    found = {
        path.relative_to(CORE).as_posix(): violations(path.read_text(), _role(path))
        for path in _core_modules()
    }
    assert {path: v for path, v in found.items() if v} == {}


def test_the_core_s_calls_of_pack_code_are_where_the_sites_are() -> None:
    sites: dict[str, int] = {}
    for path in _core_modules():
        tree = ast.parse(path.read_text())
        count = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in ("call", "enter")
        )
        if count and _role(path) != "guards":
            sites[path.relative_to(CORE).as_posix()] = count
    assert sites == {
        "analyses/packs.py": 1,
        "analyses/registry.py": 1,
        "catalog/cohorts.py": 1,
        "catalog/index.py": 1,
        "engine/canonical.py": 1,
        "engine/resolve.py": 2,
        "importers/run.py": 2,
        "store/proposals.py": 1,
        "store/writes.py": 2,
    }


def test_copiers_is_a_literal_tuple_of_the_copiers() -> None:
    assert frozenset(f.__name__ for f in copiers.COPIERS) == COPIER_NAMES
    assert len(COPIER_NAMES) == 9


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_violation_of_each_rule_is_found(name: str) -> None:
    rule, role, source = CASES[name]
    assert {v.rule for v in violations(source, role)} == {rule}


def test_the_violations_cover_every_rule() -> None:
    rules = {rule for rule, _, _ in CASES.values()}
    assert rules == {
        "call",
        "run",
        "hook",
        "argument",
        "copier",
        "allowance",
        "view",
        "slots",
        "contain",
        "handler",
        "state",
        "enter",
        "getattribute",
    }

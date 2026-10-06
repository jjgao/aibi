"""What the tests that read ``src`` as syntax (D399's rule, its census of mappings written as
segments, the escape ban of D398) share: where a node is, what a constant is, what a module
defines. A module of its own, as ``_segments.py`` is; A1's census script (``scripts/``) is
standalone and imports nothing of the repository, so it keeps its own walk."""

import ast
from collections.abc import Iterable

Function = ast.FunctionDef | ast.AsyncFunctionDef
Scope = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def qualified(scopes: Iterable[Scope]) -> str:
    """The dotted name of the nested functions and classes, ``<module>`` for none."""
    return ".".join(scope.name for scope in scopes) or "<module>"


def scopes(tree: ast.AST) -> dict[int, str]:
    """The qualified name of the function or class that encloses each node, by ``id``."""
    found: dict[int, str] = {}

    def visit(node: ast.AST, inside: list[Scope]) -> None:
        found[id(node)] = qualified(inside)
        inner = [*inside, node] if isinstance(node, Scope) else inside
        for child in ast.iter_child_nodes(node):
            visit(child, inner)

    visit(tree, [])
    return found


def string(node: ast.AST | None) -> str | None:
    """The value of a ``str`` constant, ``None`` for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def defined(tree: ast.AST) -> set[str]:
    """Every name ``tree`` defines or assigns: a function, a class, a variable, an attribute."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, Scope):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            names.add(node.attr)
    return names

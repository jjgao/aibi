"""A census of the sites where a string reaches server text (SPEC §8.1, §14, D397).

A ``text`` segment holds only the server's words: what came from a source, an operator, an agent,
a document, a configuration value or a library's message is a ``data`` segment. This script finds,
from the syntax tree alone, every call whose argument becomes server text, so that the sites that
remain are listed and not remembered:

1. The *wrappers* are found by a fixpoint. ``text`` and ``TextSegment`` are the seeds. A function,
   or a class by its ``__init__``, is a wrapper when one of its ``str`` parameters carries into an
   argument of a call of a wrapper: by itself, in an f-string, a concatenation, a slice, a
   conditional, ``str()``, ``.join`` or ``.format``, through a loop, a comprehension or an
   assignment. ``StoreRefused``, ``EditRefused``, ``refused()``, ``SourceError`` and the rest are
   found this way, and a new one is found without being named. Functions are known by their names,
   which over-approximates: two functions of one name are one wrapper.
2. A *site* is an argument of a wrapper, at a parameter that reaches text, that is not a string
   literal and not a segment (a call of ``data``, ``shown``, ``listed``, or a function
   annotated to return a ``Segment`` or ``Message``): an f-string is a site for each of its holes,
   and a parameter a wrapper passes on is a site where it is called, not where it is passed. An
   exception that is raised with a message and caught where ``str(error)`` goes to a wrapper is a
   wrapper too: its constructor's arguments are sites, marked ``caught``.
   A segment written as a raw ``{"text": ...}`` dict is a site too, for its value.
3. A site is *reviewed* when its key has a reason in ``census_text_sites.reviewed.tsv`` (or a rule
   of ``RULES``, which are written as what the hole is, a constant or an enumeration member, and
   never as what it is called). The key is the file, the enclosing function's qualified name, the
   wrapper and the argument's source text (``*reason`` for a starred one), so a new site, a reverted
   conversion or a variable of the same name elsewhere inherits no review. The rest are the
   residue, each to be converted to a segment or listed for M4.0f-A2, whose class rule closes this
   by type. The script fails (status 1) on a reviewed key that matches no site (a stale entry), and
   its output is the residue, which ``tests/core/test_census_text_sites.py`` holds equal to
   ``census_text_sites.residue.md``.

What it cannot see: a string built in one function and wrapped in another is listed where it is
passed (a bare name), not where it was built, and the reviewer follows it; a value that is not a
string at the call but one later (a ``Path``, an ``int``) is a hole whose type the census does not
know, so its reviewed reasons say what each is.

Run it from ``server/`` with ``uv run`` (it needs Python 3.12 or later, as the project does). It
imports nothing of aibi; the test imports it as a module by its path, and nothing else does:

    uv run python scripts/census_text_sites.py            # the residue, as a Markdown table
    uv run python scripts/census_text_sites.py --all      # every site, with line and reason
    uv run python scripts/census_text_sites.py --wrappers # the wrappers the fixpoint found
    uv run python scripts/census_text_sites.py --root DIR # the aibi package at DIR (src/aibi)
"""

import ast
import re
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "aibi"
"""The package to read; ``--root DIRECTORY`` reads the ``aibi`` package at DIRECTORY instead (a
checkout's ``src/aibi``)."""
SEEDS = {"text", "TextSegment"}
SEGMENTS = {"data", "shown", "listed", "DataSegment"}
"""Calls whose result is a segment, or whose argument becomes one: what they hold is data."""
CARRIERS = {"str", "repr", "format", "join", "plain_text", "strip", "lower", "upper", "title"}
"""Functions and methods whose result is made of their argument's characters."""

Function = ast.FunctionDef | ast.AsyncFunctionDef

NOT_WRAPPERS = {
    # lookups and renderers that share a name with a function that does build a message
    "table",
    "column",
    "truth",
    "string",
    "ids",
    "_ids",
    "joined",
    "resolve",
    "primary_key",
    "free_column",
    "unique_columns",
    "leads_to_key",
    "_conditions",
    "_identifying",
    "_typed",
    "_table",
    "_restricted",
    "_of_clauses",
}
"""Names that the fixpoint takes for wrappers because another function of the name is one (two
functions of one name are one to it); reviewed, each is not."""
AMBIGUOUS: set[str] = set()
"""The wrappers some other function of whose name is not one: ``--wrappers`` marks them ``?``."""

RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"RefusalCode\.[A-Z_]+"), "an enumeration member"),
    (
        re.compile(r"_?[A-Z][A-Z0-9_]*(\[.*\])?"),
        "a constant of the server's own, or a lookup in one",
    ),
    (re.compile(r"(\w+\.)+[A-Z][A-Z0-9_]*"), "a constant of another module"),
    (re.compile(r"(self\.)?limits\.[a-z_]+"), "a limit"),
    (re.compile(r"-?\d+|[a-z]+ [-+*/] \d+|[a-z]+ [-+*/] [A-Z_]+"), "a number"),
]
"""Holes that are not data by what they are, whatever the file: a constant, an enumeration member,
a number, a member of ``limits``. Never by what a name is called (``reason``, ``_columns``) or by
the call it holds (``len(``): those are reviewed by key."""

REVIEWED_FILE = Path(__file__).with_name("census_text_sites.reviewed.tsv")
"""The reviewed sites, one per line: file, enclosing function, wrapper, argument, reason, separated
by tabs."""
Key = tuple[str, str, str, str]


def reviewed() -> dict[Key, str]:
    """The reason of each reviewed key (file, function, wrapper, argument)."""
    found: dict[Key, str] = {}
    for line in REVIEWED_FILE.read_text().splitlines():
        file, function, wrapper, hole, why = line.split("\t")
        found[file, function, wrapper, hole] = why
    return found


@dataclass
class Shape:
    """One function or class of a wrapper's name."""

    names: list[str]
    """The parameters before ``*args``, ``self`` and ``cls`` left out."""
    star: str | None
    reaching: set[str]
    """The parameters that reach server text."""
    module: str = "*"
    """The file that defines it (``*`` for the seeds, which any file reaches)."""


@dataclass
class Wrapper:
    shapes: list[Shape] = field(default_factory=list[Shape])

    @property
    def reaching(self) -> set[str]:
        return set().union(*(shape.reaching for shape in self.shapes))


def _name(function: ast.expr) -> str | None:
    if isinstance(function, ast.Name):
        return function.id
    return function.attr if isinstance(function, ast.Attribute) else None


def _carries(node: ast.AST, names: set[str]) -> bool:
    """Whether the characters of a name in ``names`` can be in what ``node`` makes. A call of any
    function but the string functions is not followed: what it returns is its own."""
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.JoinedStr):
        return any(
            _carries(part.value, names)
            for part in node.values
            if isinstance(part, ast.FormattedValue)
        )
    if isinstance(node, ast.BinOp):
        return _carries(node.left, names) or _carries(node.right, names)
    if isinstance(node, ast.IfExp):
        return _carries(node.body, names) or _carries(node.orelse, names)
    if isinstance(node, ast.Subscript | ast.Starred):
        return _carries(node.value, names)
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return any(_carries(element, names) for element in node.elts)
    if isinstance(node, ast.ListComp | ast.GeneratorExp | ast.SetComp):
        looped = {
            n.id
            for generator in node.generators
            if _carries(generator.iter, names)
            for n in ast.walk(generator.target)
            if isinstance(n, ast.Name)
        }
        return _carries(node.elt, names | looped)
    if isinstance(node, ast.Call) and _name(node.func) in CARRIERS:
        if isinstance(node.func, ast.Attribute) and _carries(node.func.value, names):
            return True
        return any(_carries(argument, names) for argument in node.args)
    return False


def _functions(tree: ast.AST) -> Iterator[tuple[str, Function]]:
    """The functions, and a class's ``__init__`` under the class's name."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name == "__init__":
                    yield node.name, member
        elif isinstance(node, Function) and node.name != "__init__":
            yield node.name, node


def _parameters(function: Function) -> tuple[list[ast.arg], list[ast.arg]]:
    """The positional parameters, ``self`` and ``cls`` left out, and the keyword-only ones."""
    positional = [*function.args.posonlyargs, *function.args.args]
    if positional and positional[0].arg in ("self", "cls"):
        positional = positional[1:]
    return positional, list(function.args.kwonlyargs)


def _strings(function: Function) -> set[str]:
    """The parameters that can hold a string: annotated with ``str``, or not annotated."""
    positional, keyword = _parameters(function)
    given = [*positional, *keyword, *([function.args.vararg] if function.args.vararg else [])]
    return {a.arg for a in given if a.annotation is None or "str" in ast.unparse(a.annotation)}


def _derived(function: Function, names: set[str]) -> set[str]:
    """``names`` and what a loop, a comprehension or an assignment makes of them."""
    found = set(names)
    for _ in range(3):
        for node in ast.walk(function):
            if isinstance(node, ast.comprehension | ast.For) and _carries(node.iter, found):
                found |= {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
            elif (
                isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign)
                and node.value
                and _carries(node.value, found)
            ):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                found |= {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
    return found


def _callable(call: ast.Call, found: Wrapper, module: str, imported: set[str]) -> list[Shape]:
    """The functions of the name that ``call`` can be of: for a name or a method of ``self``, one
    that its file defines or imports; for a method of another object, any of the name."""
    function = call.func
    if isinstance(function, ast.Attribute) and function.attr in SEEDS:
        module_function = isinstance(function.value, ast.Name) and function.value.id == "output"
        return found.shapes if module_function else []  # a method called ``text`` is not ours
    if isinstance(function, ast.Attribute) and not (
        isinstance(function.value, ast.Name) and function.value.id in ("self", "cls")
    ):
        return found.shapes
    name = _name(function)
    return [
        shape
        for shape in found.shapes
        if shape.module in ("*", module)
        or (name in imported and not isinstance(function, ast.Attribute))
    ]


def _arguments(
    call: ast.Call, found: Wrapper, module: str, imported: set[str]
) -> Iterator[tuple[str, ast.expr]]:
    """The arguments of ``call`` at a parameter that reaches text, of any function it can be of."""
    for shape in _callable(call, found, module, imported):
        for at, argument in enumerate(call.args):
            if isinstance(argument, ast.Starred):
                if shape.star in shape.reaching:
                    yield shape.star or "*", argument.value
            elif at < len(shape.names):
                if shape.names[at] in shape.reaching:
                    yield shape.names[at], argument
            elif shape.star in shape.reaching:
                yield shape.star or "*", argument
        for keyword in call.keywords:
            if keyword.arg in shape.reaching:
                yield keyword.arg or "", keyword.value


def _imports(trees: Mapping[str, ast.AST]) -> dict[str, set[str]]:
    """The names each file imports from another: ``from x import name as alias``."""
    return {
        file: {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        for file, tree in trees.items()
    }


def segment_makers(trees: Mapping[str, ast.AST]) -> set[str]:
    """Functions that return a segment or a message, by their annotation (``shown``, ``_name``)."""
    found = set(SEGMENTS)
    for tree in trees.values():
        for _, function in _functions(tree):
            returned = ast.unparse(function.returns) if function.returns else ""
            if "Segment" in returned or "Message" in returned:
                found.add(function.name)
    return found


def _holes(argument: ast.expr, makers: set[str]) -> list[ast.expr]:
    """What of an argument is not a literal and not a segment: the holes of an f-string, the
    non-literal parts of a concatenation or a conditional, or the argument itself. A call of
    ``text`` is a segment here: it is a site where its own argument is."""
    if isinstance(argument, ast.Constant):
        return []
    if isinstance(argument, ast.JoinedStr):
        return [part.value for part in argument.values if isinstance(part, ast.FormattedValue)]
    if isinstance(argument, ast.BinOp) and isinstance(argument.op, ast.Add):
        return _holes(argument.left, makers) + _holes(argument.right, makers)
    if isinstance(argument, ast.IfExp):
        return _holes(argument.body, makers) + _holes(argument.orelse, makers)
    if isinstance(argument, ast.Call) and _name(argument.func) in makers | SEEDS:
        return []
    return [argument]


def wrappers(trees: Mapping[str, ast.AST], makers: set[str]) -> dict[str, Wrapper]:
    """The fixpoint. A name is a wrapper if any function or class of the name is one (the census
    errs toward more sites); ``AMBIGUOUS`` names the wrappers that some other function of the
    name is not, which the review of the list either confirms or puts in ``NOT_WRAPPERS``."""
    imports = _imports(trees)
    defined: dict[str, list[tuple[str, Function]]] = {}
    for file, tree in trees.items():
        for name, function in _functions(tree):
            if name not in SEEDS and name not in NOT_WRAPPERS and not name.startswith("test_"):
                defined.setdefault(name, []).append((file, function))
    found = {
        "text": Wrapper([Shape(["value"], None, {"value"})]),
        "TextSegment": Wrapper([Shape([], None, {"text"})]),
    }
    changed = True
    while changed:
        changed = False
        for name, functions in defined.items():
            shapes: list[Shape] = []
            for file, function in functions:
                derived = _derived(function, _strings(function))
                reaching: set[str] = set()
                for call in ast.walk(function):
                    callee = _name(call.func) if isinstance(call, ast.Call) else None
                    if not isinstance(call, ast.Call) or callee not in found or callee == name:
                        continue
                    for _, argument in _arguments(call, found[callee], file, imports[file]):
                        if not (isinstance(argument, ast.Call) and _name(argument.func) in makers):
                            reaching |= {p for p in derived if _carries(argument, {p})}
                roots = {p for p in _strings(function) if reaching & _derived(function, {p})}
                positional, _ = _parameters(function)
                star = function.args.vararg.arg if function.args.vararg else None
                shapes.append(Shape([a.arg for a in positional], star, roots, file))
            if not any(shape.reaching for shape in shapes):
                continue
            if not all(shape.reaching for shape in shapes):
                AMBIGUOUS.add(name)
            if name not in found or found[name].shapes != shapes:
                found[name] = Wrapper(shapes)
                changed = True
    return found


def _caught(trees: Mapping[str, ast.AST], found: dict[str, Wrapper]) -> set[str]:
    """The exception classes caught where the exception's ``str`` goes to a wrapper."""
    classes: set[str] = set()
    imports = _imports(trees)
    for file, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.name is None or node.type is None:
                continue
            for call in ast.walk(node):
                callee = _name(call.func) if isinstance(call, ast.Call) else None
                if (
                    isinstance(call, ast.Call)
                    and callee in found
                    and any(
                        _carries(a, {node.name})
                        for _, a in _arguments(call, found[callee], file, imports[file])
                    )
                ):
                    types = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
                    classes |= {n for t in types if (n := _name(t))}
    return classes


@dataclass(frozen=True)
class Site:
    file: str
    line: int
    function: str
    """The enclosing function's qualified name (``Class.method``, ``outer.inner``), or the class
    or ``<module>`` for a call outside any function."""
    wrapper: str
    hole: str
    caught: bool = False

    @property
    def key(self) -> Key:
        return self.file, self.function, self.wrapper, self.hole

    def reason(self, known: dict[Key, str]) -> str | None:
        for rule, why in RULES:
            if rule.fullmatch(self.hole):
                return why
        return known.get(self.key)


def _shortened(node: ast.AST) -> str:
    written = ast.unparse(node).replace("\n", " ")
    return written if len(written) <= 90 else written[:87] + "..."


def _scopes(tree: ast.AST) -> dict[int, str]:
    """The qualified name of the function, or class, that encloses each node."""
    found: dict[int, str] = {}

    def visit(node: ast.AST, scope: list[str]) -> None:
        found[id(node)] = ".".join(scope) or "<module>"
        inner = [*scope, node.name] if isinstance(node, ast.ClassDef | Function) else scope
        for child in ast.iter_child_nodes(node):
            visit(child, inner)

    visit(tree, [])
    return found


def _raw_segments(tree: ast.AST, makers: set[str]) -> Iterator[ast.expr]:
    """The values of a ``"text"`` key that are not literals, in a dict written as a segment."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "text":
                    yield from _holes(value, makers)


def census() -> tuple[dict[str, Wrapper], list[Site]]:
    trees = {
        path.relative_to(ROOT).as_posix(): ast.parse(path.read_text())
        for path in sorted(ROOT.rglob("*.py"))
        if path.relative_to(ROOT).parts[0] != "packs"
    }
    makers = segment_makers(trees)
    found = wrappers(trees, makers)
    caught = _caught(trees, found)
    imports = _imports(trees)
    sites: set[Site] = set()
    for file, tree in trees.items():
        scopes = _scopes(tree)
        for hole in _raw_segments(tree, makers):  # a segment written as a dict
            sites.add(Site(file, hole.lineno, scopes[id(hole)], '{"text": ...}', _shortened(hole)))
        inside: dict[int, tuple[str, Function]] = {}
        for owner, function in _functions(tree):  # an inner function's calls are its own
            inside |= {id(node): (owner, function) for node in ast.walk(function)}
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            callee = _name(call.func) or ""
            if callee in found:
                arguments = list(_arguments(call, found[callee], file, imports[file]))
                marked = False
            elif callee in caught:
                arguments = [("message", argument) for argument in call.args]
                marked = True
            else:
                continue
            starred = {id(a.value) for a in call.args if isinstance(a, ast.Starred)}
            owner, function = inside.get(id(call), ("", None))
            own = (
                _derived(function, _strings(function) & found[owner].reaching)
                if function and owner in found
                else set[str]()
            )
            for _, argument in arguments:
                for hole in _holes(argument, makers):
                    if isinstance(hole, ast.Name) and hole.id in own:
                        continue  # passed on: a site where this function is called
                    written = ("*" if id(hole) in starred else "") + _shortened(hole)
                    sites.add(Site(file, hole.lineno, scopes[id(call)], callee, written, marked))
    return found, sorted(sites, key=lambda s: (s.file, s.line, s.hole, s.wrapper))


HOLE = "{}"
"""What a template holds where its argument is not a literal."""


def _constants(trees: Mapping[str, ast.AST]) -> dict[str, list[str]]:
    """The strings of each module constant made of literals (a string, or a dict, tuple or list
    of them), by its name, in every file: a name two files define holds both's."""
    found: dict[str, list[str]] = {}
    for tree in trees.values():
        for node in getattr(tree, "body", []):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                target, value = node.target, node.value
            else:
                continue
            strings = _literals(value)
            if isinstance(target, ast.Name) and strings:
                found.setdefault(target.id, []).extend(strings)
    return found


def _literals(node: ast.expr) -> list[str] | None:
    """The strings of a literal made only of strings (keys of a dict left out), or ``None``."""
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else None
    if isinstance(node, ast.Tuple | ast.List):
        parts = [_literals(element) for element in node.elts]
    elif isinstance(node, ast.Dict):
        parts = [_literals(value) for value in node.values]
    else:
        return None
    if not parts or any(part is None for part in parts):
        return None
    return [string for part in parts if part is not None for string in part]


def _templates(argument: ast.expr, makers: set[str], constants: dict[str, list[str]]) -> list[str]:
    """What an argument that reaches server text can be, as templates: its literal text, a hole
    ``{}`` for each part that is not a literal, and every string of a module constant it names."""
    if isinstance(argument, ast.Constant):
        return [argument.value] if isinstance(argument.value, str) else [HOLE]
    if isinstance(argument, ast.JoinedStr):
        return [
            "".join(
                str(part.value) if isinstance(part, ast.Constant) else HOLE
                for part in argument.values
            )
        ]
    if isinstance(argument, ast.BinOp) and isinstance(argument.op, ast.Add):
        return [
            left + right
            for left in _templates(argument.left, makers, constants)
            for right in _templates(argument.right, makers, constants)
        ]
    if isinstance(argument, ast.IfExp):
        return _templates(argument.body, makers, constants) + _templates(
            argument.orelse, makers, constants
        )
    if isinstance(argument, ast.Call) and _name(argument.func) in makers | SEEDS:
        return []
    named = argument.value if isinstance(argument, ast.Subscript) else argument
    name = _name(named) if isinstance(named, ast.Name | ast.Attribute) else None
    if name is not None and name in constants:
        return list(constants[name])
    return [HOLE]


def templates() -> list[str]:
    """Every template of the server's text, sorted: what reaches ``text()`` and the functions
    whose argument becomes server text (the census's wrappers, ``refused`` and ``StoreRefused``
    among them), and a segment written as ``{"text": ...}``. Their digest is checked in with
    the server's wording (``CACHE_WORDING``, D399), so that no template, even one no golden case
    renders, changes without it; it is a multiset, so moving code does not change it."""
    trees = {
        path.relative_to(ROOT).as_posix(): ast.parse(path.read_text())
        for path in sorted(ROOT.rglob("*.py"))
        if path.relative_to(ROOT).parts[0] != "packs"
    }
    makers = segment_makers(trees)
    found = wrappers(trees, makers)
    imports = _imports(trees)
    constants = _constants(trees)
    written: list[str] = []
    for file, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if isinstance(key, ast.Constant) and key.value == "text":
                        written += _templates(value, makers, constants)
            if not isinstance(node, ast.Call):
                continue
            callee = _name(node.func) or ""
            if callee not in found:
                continue
            for _, argument in _arguments(node, found[callee], file, imports[file]):
                written += _templates(argument, makers, constants)
    return sorted(written)


def stale(sites: list[Site], known: dict[Key, str]) -> list[Key]:
    """The reviewed keys that no site has: a review of what is no longer there."""
    present = {site.key for site in sites}
    return sorted(key for key in known if key not in present)


def report(found: dict[str, Wrapper], sites: list[Site], known: dict[Key, str], every: bool) -> str:
    """The residue as a Markdown table, without lines (they move), and its totals; with ``every``,
    each site with its line and reason."""
    shown = [s for s in sites if every or s.reason(known) is None]
    if every:
        head = ["| site | function | wrapper | hole | reason |", "|---|---|---|---|---|"]
        rows = [
            f"| {s.file}:{s.line} | {s.function} | {s.wrapper}{' (caught)' if s.caught else ''} "
            f"| `{s.hole}` | {s.reason(known) or ''} |"
            for s in shown
        ]
    else:
        head = ["| file | function | wrapper | hole |", "|---|---|---|---|"]
        rows = [
            f"| {s.file} | {s.function} | {s.wrapper}{' (caught)' if s.caught else ''} "
            f"| `{s.hole}` |"
            for s in shown
        ]
    totals = f"Census: {len(shown)} sites of {len(sites)}; {len(found)} wrappers"
    return "\n".join([*head, *rows, totals])


def main(argv: list[str]) -> int:
    global ROOT
    if "--root" in argv:
        ROOT = Path(argv[argv.index("--root") + 1]).resolve()
    found, sites = census()
    if "--wrappers" in argv:
        for name in sorted(found):
            ambiguous = "?" if name in AMBIGUOUS else ""
            print(f"{name}{ambiguous}({', '.join(sorted(found[name].reaching))})")
        return 0
    known = reviewed()
    print(report(found, sites, known, "--all" in argv))
    gone = stale(sites, known)
    for key in gone:
        print(f"stale review, no such site: {key}", file=sys.stderr)
    return 1 if gone else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

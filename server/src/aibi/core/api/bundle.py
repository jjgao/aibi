"""The web bundle: its loader, its two documents and its routes (SPEC §12.4, §14; D410–D412).

**Trust.** The bundle's JavaScript and CSS are the operator's code, as trusted as a pack: the
operator built and chose them. Nothing in the bundle is parsed for safety; what is guarded is
what the server writes and serves. Untrusted are every request, its path and headers, an
embedder's root path and mounts, and the configured path itself.

**Loading** (``load_bundle``, once at start; a changed bundle needs a restart). The path
``[server] web_bundle`` names is resolved once, a component at a time (``swappable_way``), and the
real directory opened, and every component below it relative to its directory's descriptor
without following a link (``O_NOFOLLOW``, ``O_DIRECTORY`` for directories), each file checked on
its descriptor (``fstat``: a regular file) before it is read into memory, at most its cap of bytes
whatever size it gave. Only ``.vite/manifest.json`` and the files of ``assets/`` are read. The
loader refuses (``BundleError``) a symbolic link or a file that is not regular anywhere it looks;
a subdirectory of ``assets/``; anything at the root but ``.vite/``, ``assets/`` and the build's
own ``index.html`` and ``operator.html`` (present or not, never read, and checked for their type
alone); anything in ``.vite/`` but ``manifest.json``; a name in ``assets/`` outside ``ASSET_NAME``
or holding ``..``; an extension but ``.js``, ``.css``, ``.svg`` and ``.png`` (no fonts, no source
maps); more than ``MAX_FILES`` files, a file over ``MAX_FILE_BYTES`` and files over
``MAX_BUNDLE_BYTES`` together; every error of the system below the root (a listing, an ``lstat``,
an ``fstat``, an open, a read, a close), as when a build rewrites the directory while the server
starts; and a manifest that the core's strict JSON parser refuses (invalid UTF-8, duplicate keys,
non-finite numbers) or whose known members have another type (unknown members are ignored).

**Trust** in the files is D253's rule for the configuration, applied to the bundle: the root, its
``.vite/`` and ``assets/`` and every file read must be owned by the server's user or root
(``server_owns``) and not writable by group or others (``UNSAFE_MODE``), and the way to the root
(a link on it, and the root's own directory) must not be one that others could swap
(``swappable_ways``). A file with several names, as an install by hard links gives, is as trusted
as one with a single name. These problems are collected over the whole load and refused as one
``BundleError``: the first ``MAX_LISTED`` of them, how many more, and the remedy once, after any
other refusal the walk found first: commands, each a line of its own that a shell runs as it
stands, naming the real root (never a link to it, which ``chown -R`` does not follow) and the
directories on the way (``chmod go-w``, ``chmod -R go-w``, ``chown -R -h``), which clear what was
found. What is held after start is what was read: the bundle is in memory, so a file replaced
later changes nothing until a restart, but the permissions are checked at load only, and a bundle
that others can write between one start and the next is not guarded; nor are the directories above
the root's own (the operator keeps them, as for the configuration and for a pack). Every text of
the bundle in a refusal (a name, a manifest key or value) is shown as ``repr`` shows it (A6).

**The documents are the server's** (``document``): a fixed template (doctype, ``<meta
charset>``, a fixed title, the entry's ``<script type="module" crossorigin>``, a ``modulepreload``
link for each chunk of its static imports, a ``stylesheet`` link for each stylesheet they and it
load, each with ``crossorigin``, and ``<div id="root">``), whose names come from the manifest by
Vite's own rule: each ``imports`` and ``dynamicImports`` key is resolved through the manifest (a
key it lacks refuses start); the static imports' closure is walked with a visited set (a cycle
is legal and ends), the chunks in post-order, the stylesheets depth first, each once. Only the
values of ``file``, ``css`` and ``assets`` are names, each ``assets/<name>`` with a name the
dictionary of files holds (else start is refused); an entry's or a chunk's ``file`` is a
``.js`` file and a ``css`` value a ``.css`` file. ``index.html`` is a manifest entry
(``isEntry``) or start is refused; ``operator.html`` is optional, and without it ``/curate`` is
``NOT_FOUND``. There is no icon link: ``/favicon.ico`` stays the page path's ``NOT_FOUND``.

**Serving** (``bundle_router``, FastAPI ``GET`` routes, so ``HEAD`` is ``METHOD_NOT_ALLOWED``):
``GET /`` and ``GET /datasets/<one segment>`` answer the catalogue document, any query ignored
(they replace the catalogue page of D311 there, and only there); ``GET /curate`` the operator
document; ``GET /assets/<name>`` a file by an exact lookup of its name, never a path joined, and
any other name is ``NOT_FOUND``. Documents are ``text/html; charset=utf-8`` with
``DOCUMENT_HEADERS`` (``DOCUMENT_POLICY``, ``X-Frame-Options: DENY``, ``Cache-Control:
no-store``, ``Cross-Origin-Opener-Policy: same-origin``); a file is of the type its extension
gives in ``CONTENT_TYPES`` (never ``mimetypes``), under request protection's default policy, and
``Cache-Control: public, max-age=31536000, immutable`` only when the manifest names it as an
output and its name is the manifest's own name for it and a hash (``hashed``), ``no-cache``
otherwise. The
bundle is served only at the origin root: under any other root path, as an embedder's mount
gives, every route refuses loudly, ``INTERNAL_ERROR`` logged, since the documents' absolute
``/assets/`` names would load another application's files and the operator entry would send the
token to the origin root's ``/operator``. Whether a request is at a page path, and its root path,
are what request protection recorded (``classify``, D414).
"""

import logging
import os
import pwd
import re
import shlex
import stat
from collections.abc import Collection, Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from starlette.responses import Response

from aibi.core.api.chrome import ASSETS_PREFIX, CATALOGUE_PATH, CURATE_PATH, DATASETS_PREFIX
from aibi.core.api.config import server_owns, swappable_ways
from aibi.core.api.errors import refusal, refused, refused_page
from aibi.core.api.logs import WithoutSecrets
from aibi.core.classify import classified
from aibi.core.schema.jsonio import JsonError, parse_json
from aibi.core.schema.refusals import RefusalCode

ASSET_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")
"""A file name of ``assets/``; a name holding ``..`` is refused besides. It may begin with ``_``,
as Rollup's chunks do (``_commonjsHelpers-<hash>.js``), but not with ``.`` or ``-``."""
CONTENT_TYPES: Mapping[str, str] = MappingProxyType(
    {
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
    }
)
"""The extensions a bundle's file may have, and the type each is served as."""
HASH = re.compile(r"[A-Za-z0-9_-]{8}")
"""A hash of the build's names, ``[name]-[hash]``: 8 characters of ``[A-Za-z0-9_-]`` (D411)."""
UNSAFE_MODE = stat.S_IWGRP | stat.S_IWOTH
"""The permission bits a file or directory of the bundle must not have: write for group or
others."""
MAX_LISTED = 20
"""The permission problems a refusal lists; it counts the rest."""
MAX_FILES = 2_000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
"""The bytes of the files of ``assets/`` together."""
MANIFEST_DIRECTORY = ".vite"
MANIFEST = "manifest.json"
ASSETS = "assets"
CATALOGUE_ENTRY = "index.html"
OPERATOR_ENTRY = "operator.html"
_ROOT_FILES = frozenset({CATALOGUE_ENTRY, OPERATOR_ENTRY})
"""The build's own documents, which may lie at the root and are never read."""
DOCUMENT_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
    "require-trusted-types-for 'script'; trusted-types 'none'"
)
"""The documents' policy (D411): the bundle's own scripts, stylesheets, images and connections,
nothing inline, no Trusted Types policy, so that no string becomes markup or script."""
DOCUMENT_HEADERS: Mapping[str, str] = MappingProxyType(
    {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Security-Policy": DOCUMENT_POLICY,
        "X-Frame-Options": "DENY",
        "Cache-Control": "no-store",
        "Cross-Origin-Opener-Policy": "same-origin",
    }
)
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
CATALOGUE_TITLE = "aibi"
OPERATOR_TITLE = "aibi operator"
_OPEN_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_OPEN_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_logger = logging.getLogger(__name__)
_logger.addFilter(WithoutSecrets())


class BundleError(Exception):
    """A web bundle refused, with the problems found."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = tuple(problems)


@dataclass(frozen=True)
class Asset:
    """A file of the bundle, as it is served."""

    body: bytes = field(repr=False)
    content_type: str
    immutable: bool


@dataclass(frozen=True)
class Bundle:
    """A loaded bundle: its files by name, and the two documents the server wrote."""

    assets: Mapping[str, Asset] = field(repr=False)
    catalogue: bytes = field(repr=False)
    operator: bytes | None = field(repr=False)
    """``None`` when the manifest has no ``operator.html`` entry."""

    @property
    def files(self) -> int:
        return len(self.assets)

    @property
    def size(self) -> int:
        """The bytes of its files together."""
        return sum(len(found.body) for found in self.assets.values())


# --- The manifest -----------------------------------------------------------------------------


class _Chunk(BaseModel):
    """A manifest entry's members the server reads; Vite's others are ignored."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    file: str
    name: str | None = None
    is_entry: bool = Field(default=False, alias="isEntry")
    imports: list[str] = Field(default_factory=list[str])
    dynamic_imports: list[str] = Field(default_factory=list[str], alias="dynamicImports")
    css: list[str] = Field(default_factory=list[str])
    assets: list[str] = Field(default_factory=list[str])


_MANIFEST: TypeAdapter[dict[str, _Chunk]] = TypeAdapter(dict[str, _Chunk])


def _manifest(source: bytes) -> dict[str, _Chunk] | str:
    try:
        parsed = parse_json(source)
    except JsonError as error:
        return f"{MANIFEST_DIRECTORY}/{MANIFEST}: not strict JSON ({error.message})"
    try:
        return _MANIFEST.validate_python(parsed)
    except ValidationError as error:
        where = ", ".join(
            "/".join(repr(part) for part in details["loc"]) or "(the manifest)"
            for details in error.errors(include_url=False, include_input=False)
        )
        return f"{MANIFEST_DIRECTORY}/{MANIFEST}: not a Vite manifest (at {where})"


def _name(value: str) -> str | None:
    """The file name of a manifest's ``assets/<name>``, if it is one."""
    prefix = ASSETS + "/"
    if not value.startswith(prefix):
        return None
    name = value[len(prefix) :]
    return name if ASSET_NAME.fullmatch(name) is not None and ".." not in name else None


@dataclass(frozen=True)
class _Documents:
    catalogue: bytes
    operator: bytes | None
    outputs: Mapping[str, frozenset[str]]
    """The files the manifest names as outputs, each with the stems the build would have given
    its name (``_stems``), which may be none."""


def _stems(key: str, chunk: _Chunk, member: str) -> list[str]:
    """What the build would call the file that ``chunk`` (manifest key ``key``) gives as
    ``member``, before ``-<hash>``: a chunk's own name for its script and its stylesheets, and
    for a file without a name (an image) the stem of its key's file name; nothing for an asset
    the chunk only loads, whose own entry gives its stem."""
    if member == "assets":
        return []
    if chunk.name is not None:
        return [chunk.name]
    stem = os.path.splitext(key.rpartition("/")[2])[0]
    return [stem] if member == "file" and not chunk.file.endswith(".js") else []


def hashed(name: str, stems: Collection[str]) -> bool:
    """Whether a file of ``assets/`` is named as the build names an output, ``<stem>-<hash>``
    before its extension, for one of ``stems`` (D411). A name the manifest does not account for
    this way is not hashed, however it looks: ``survival-km-chart.js`` is not."""
    base = os.path.splitext(name)[0]
    return any(
        base.startswith(stem + "-") and HASH.fullmatch(base[len(stem) + 1 :]) is not None
        for stem in stems
    )


def document(title: str, script: str, preloads: Sequence[str], styles: Sequence[str]) -> bytes:
    """A document of the fixed template, naming the bundle's files ``script``, ``preloads`` and
    ``styles``, each a name ``ASSET_NAME`` admits (else ``ValueError``)."""
    for name in (script, *preloads, *styles):
        if ASSET_NAME.fullmatch(name) is None or ".." in name:
            raise ValueError("a document names only the bundle's files")
    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{title}</title>",
        f'<script type="module" crossorigin src="{ASSETS_PREFIX}/{script}"></script>',
        *(f'<link rel="modulepreload" crossorigin href="{ASSETS_PREFIX}/{n}">' for n in preloads),
        *(f'<link rel="stylesheet" crossorigin href="{ASSETS_PREFIX}/{n}">' for n in styles),
        "</head>",
        '<body><div id="root"></div></body>',
        "</html>",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _documents(
    manifest: Mapping[str, _Chunk], files: Mapping[str, object]
) -> _Documents | list[str]:
    """The documents of the manifest's two entries, and the names it gives as outputs; or the
    problems that refuse them."""
    problems: list[str] = []
    outputs: dict[str, set[str]] = {}
    where = f"{MANIFEST_DIRECTORY}/{MANIFEST}"
    for key, chunk in sorted(manifest.items()):
        for member, values in (
            ("file", [chunk.file]),
            ("css", chunk.css),
            ("assets", chunk.assets),
        ):
            for value in values:
                name = _name(value)
                if name is None or name not in files:
                    problems.append(
                        f"{where}: {key!r} names {value!r} ({member}), not a file of assets/"
                    )
                    continue
                outputs.setdefault(name, set()).update(_stems(key, chunk, member))
                if member == "css" and not name.endswith(".css"):
                    problems.append(f"{where}: {key!r} names {value!r} as a stylesheet")
        for imported in (*chunk.imports, *chunk.dynamic_imports):
            if imported not in manifest:
                problems.append(f"{where}: {key!r} imports {imported!r}, which it does not hold")
    for key in (CATALOGUE_ENTRY, OPERATOR_ENTRY):
        found = manifest.get(key)
        if found is None and key == OPERATOR_ENTRY:
            continue
        if found is None or not found.is_entry:
            problems.append(f"{where}: {key!r} is not an entry of the manifest")
    for key in _chunks(manifest):
        if not manifest[key].file.endswith(".js"):
            problems.append(f"{where}: the chunk {key!r} is not a script")
    if problems:
        return problems
    operator = manifest.get(OPERATOR_ENTRY)
    return _Documents(
        catalogue=_entry(manifest, CATALOGUE_ENTRY, CATALOGUE_TITLE),
        operator=None if operator is None else _entry(manifest, OPERATOR_ENTRY, OPERATOR_TITLE),
        outputs={name: frozenset(stems) for name, stems in outputs.items()},
    )


def _chunks(manifest: Mapping[str, _Chunk]) -> list[str]:
    """The keys of the entries and of every chunk they import, statically or dynamically."""
    pending = [key for key in (CATALOGUE_ENTRY, OPERATOR_ENTRY) if key in manifest]
    seen = set(pending)
    while pending:
        chunk = manifest[pending.pop()]
        for imported in (*chunk.imports, *chunk.dynamic_imports):
            if imported in manifest and imported not in seen:
                seen.add(imported)
                pending.append(imported)
    return sorted(seen)


def _imported(manifest: Mapping[str, _Chunk], key: str) -> list[str]:
    """The chunks ``key`` imports statically, in post-order, each once (Vite's
    ``getImportedChunks``), walked with a stack rather than recursion, so that no chain of
    imports, however long, exhausts Python's."""
    seen = {key}
    found: list[str] = []
    stack: list[tuple[str, Iterator[str]]] = [(key, iter(manifest[key].imports))]
    while stack:
        current, pending = stack[-1]
        child = next((imported for imported in pending if imported not in seen), None)
        if child is None:
            stack.pop()
            if current != key:
                found.append(current)
            continue
        seen.add(child)
        stack.append((child, iter(manifest[child].imports)))
    return found


def _styles(manifest: Mapping[str, _Chunk], key: str) -> list[str]:
    """The stylesheets ``key`` and its static imports load, depth first, each once (Vite's
    ``getCssTagsForChunk``: a chunk's imports' stylesheets before its own, each chunk's imports
    walked once), with a stack rather than recursion."""
    walked: set[str] = set()
    seen: set[str] = set()
    found: list[str] = []

    def enter(chunk: str) -> Iterator[str]:
        if chunk in walked:
            return iter(())
        walked.add(chunk)
        return iter(manifest[chunk].imports)

    stack: list[tuple[str, Iterator[str]]] = [(key, enter(key))]
    while stack:
        current, pending = stack[-1]
        child = next(pending, None)
        if child is not None:
            stack.append((child, enter(child)))
            continue
        stack.pop()
        for value in manifest[current].css:
            if value not in seen:
                seen.add(value)
                found.append(value)
    return found


def _entry(manifest: Mapping[str, _Chunk], key: str, title: str) -> bytes:
    def name(value: str) -> str:
        found = _name(value)
        if found is None:  # checked by ``_documents``
            raise ValueError("a manifest's name is checked before its documents are written")
        return found

    return document(
        title,
        name(manifest[key].file),
        [name(manifest[chunk].file) for chunk in _imported(manifest, key)],
        [name(value) for value in _styles(manifest, key)],
    )


# --- Reading the directory --------------------------------------------------------------------


class _LoadError(Exception):
    pass


class _Trust:
    """The problems of trust a load finds, collected so that they are refused together: a mode
    that group or others can write, an owner but the server's user or root, a way to the root
    that others could swap. The refusal lists the first ``MAX_LISTED``, counts the rest and names
    the remedy once: commands that, run, clear every problem it found (``lines``)."""

    def __init__(self) -> None:
        self.problems: list[str] = []
        self.modes = False
        self.owners = False
        self.directories: dict[Path, None] = {}
        """The directories on the way that others could write, in the order found."""

    def way(self, where: str, why: str, directory: Path) -> None:
        self.problems.append(f"{where}: {why} ({directory})")
        self.directories[directory] = None

    def status(self, where: str, status: os.stat_result) -> None:
        """Check the mode and the owner of a directory or file, on its descriptor."""
        if status.st_mode & UNSAFE_MODE:
            self.modes = True
            self.problems.append(f"{where}: writable by group or others")
        if not server_owns(status.st_uid):
            self.owners = True
            self.problems.append(f"{where}: owned by another user")

    def lines(self, root: Path, real: Path) -> list[str]:
        """The lines that refuse the load for what was found, none if nothing was: the problems,
        then the commands that clear them, each a line of its own that a shell runs as it stands,
        naming the directories on the way and the real root ``real`` (a link, which ``chmod -R``
        follows and ``chown -R`` does not, is never named)."""
        if not self.problems:
            return []
        lines = self.problems[:MAX_LISTED]
        if len(self.problems) > MAX_LISTED:
            lines.append(f"... and {len(self.problems) - MAX_LISTED} more")
        commands = [f"chmod go-w {shlex.quote(str(directory))}" for directory in self.directories]
        where = shlex.quote(str(real))
        if self.modes:
            commands.append(f"chmod -R go-w {where}")
        if self.owners:
            commands.append(f"chown -R -h {shlex.quote(_server_user())} {where}")
        if commands:
            lines.append(f"{root}: to fix it, run as root:")
            lines.extend(commands)
        return lines


def _server_user() -> str:
    """The name of the user the server runs as (``root`` if it does; its number if no name)."""
    uid = os.geteuid()
    if uid == 0:
        return "root"
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _why(error: Exception) -> str:
    return (error.strerror if isinstance(error, OSError) else None) or type(error).__name__


def _entries(descriptor: int, where: str) -> dict[str, os.stat_result]:
    """The entries of the directory ``where``, each by its own ``lstat``; an entry that is
    gone, or an error of the system, refuses the bundle."""
    try:
        return {
            name: os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            for name in sorted(os.listdir(descriptor))
        }
    except OSError as error:
        raise _LoadError(f"{where}: cannot be listed ({_why(error)})") from None


def _type(status: os.stat_result) -> str | None:
    """What refuses an entry of this status where a bundle's regular file is wanted, if
    anything: another type."""
    if stat.S_ISLNK(status.st_mode):
        return "a symbolic link"
    if stat.S_ISDIR(status.st_mode):
        return "a directory"
    if not stat.S_ISREG(status.st_mode):
        return "not a regular file"
    return None


def _close(descriptor: int, where: str, *, failing: bool = False) -> None:
    """Close ``descriptor``; an error refuses the bundle, unless a refusal is already on its
    way (``failing``), which the close must not replace."""
    try:
        os.close(descriptor)
    except OSError as error:
        if not failing:
            raise _LoadError(f"{where}: cannot be closed ({_why(error)})") from None


@contextmanager
def _directory(name: str, parent: int | None, where: str, trust: _Trust) -> Generator[int]:
    """The descriptor of the directory ``name`` of ``parent`` (or of a path, without one), opened
    without following a link and checked on the descriptor: its mode and owner are noted in
    ``trust``. Closed on leaving."""
    try:
        descriptor = os.open(name, _OPEN_DIRECTORY, dir_fd=parent)
    except OSError as error:
        raise _LoadError(f"{where}: not a directory that can be opened ({_why(error)})") from None
    try:
        try:
            status = os.fstat(descriptor)
        except OSError as error:
            raise _LoadError(f"{where}: cannot be examined ({_why(error)})") from None
        trust.status(where, status)
        yield descriptor
    except BaseException:
        _close(descriptor, where, failing=True)
        raise
    _close(descriptor, where)


def _read(name: str, parent: int, where: str, limit: int, trust: _Trust) -> bytes:
    """A regular file's bytes, read from its own descriptor, at most ``limit`` of them: checked
    on the descriptor (``fstat``) before anything is read, never trusting the size it gave for
    how much to read. Its mode and owner are noted in ``trust``."""
    try:
        descriptor = os.open(name, _OPEN_FILE, dir_fd=parent)
        try:
            status = os.fstat(descriptor)
            problem = _type(status)
            if problem is not None:
                raise _LoadError(f"{where}: {problem}")
            trust.status(where, status)
            if status.st_size > limit:
                raise _LoadError(f"{where}: more than {limit} bytes")
            file = os.fdopen(descriptor, "rb")
        except BaseException:
            _close(descriptor, where, failing=True)
            raise
        with file:
            body = file.read(limit + 1)
    except OSError as error:
        raise _LoadError(f"{where}: cannot be read ({_why(error)})") from None
    if len(body) > limit:
        raise _LoadError(f"{where}: more than {limit} bytes")
    return body


def _manifest_bytes(root: int, entries: Mapping[str, os.stat_result], trust: _Trust) -> bytes:
    found = entries.get(MANIFEST_DIRECTORY)
    if found is None or not stat.S_ISDIR(found.st_mode):
        raise _LoadError(
            f"{MANIFEST_DIRECTORY}/{MANIFEST}: missing; the build writes it with build.manifest"
        )
    with _directory(MANIFEST_DIRECTORY, root, f"{MANIFEST_DIRECTORY}/", trust) as descriptor:
        inside = _entries(descriptor, f"{MANIFEST_DIRECTORY}/")
        others = sorted(name for name in inside if name != MANIFEST)
        if others or MANIFEST not in inside:
            raise _LoadError(f"{MANIFEST_DIRECTORY}/: holds {MANIFEST} alone")
        problem = _type(inside[MANIFEST])
        if problem is not None:
            raise _LoadError(f"{MANIFEST_DIRECTORY}/{MANIFEST}: {problem}")
        return _read(
            MANIFEST, descriptor, f"{MANIFEST_DIRECTORY}/{MANIFEST}", MAX_FILE_BYTES, trust
        )


def _files(root: int, entries: Mapping[str, os.stat_result], trust: _Trust) -> dict[str, bytes]:
    found = entries.get(ASSETS)
    if found is None or not stat.S_ISDIR(found.st_mode):
        raise _LoadError(f"{ASSETS}/: missing")
    with _directory(ASSETS, root, f"{ASSETS}/", trust) as descriptor:
        inside = _entries(descriptor, f"{ASSETS}/")
        if len(inside) > MAX_FILES:
            raise _LoadError(f"{ASSETS}/: more than {MAX_FILES} files")
        problems: list[str] = []
        for name, status in inside.items():
            if ASSET_NAME.fullmatch(name) is None or ".." in name:
                problems.append(f"{ASSETS}/{name!r}: not a name a bundle's file may have")
                continue
            problem = _type(status)
            if problem is not None:
                problems.append(f"{ASSETS}/{name}: {problem}")
                continue
            if os.path.splitext(name)[1] not in CONTENT_TYPES:
                problems.append(
                    f"{ASSETS}/{name}: not of a type a bundle serves ("
                    + ", ".join(CONTENT_TYPES)
                    + ")"
                )
        if problems:
            raise _LoadError("\n".join(problems))
        files: dict[str, bytes] = {}
        total = 0
        for name in inside:
            body = _read(name, descriptor, f"{ASSETS}/{name}", MAX_FILE_BYTES, trust)
            total += len(body)
            if total > MAX_BUNDLE_BYTES:
                raise _LoadError(f"{ASSETS}/: more than {MAX_BUNDLE_BYTES} bytes together")
            files[name] = body
        return files


def _root_problems(entries: Mapping[str, os.stat_result]) -> list[str]:
    problems: list[str] = []
    for name, status in entries.items():
        if name in (MANIFEST_DIRECTORY, ASSETS):
            if stat.S_ISLNK(status.st_mode):
                problems.append(f"{name}: a symbolic link")
            continue
        if name not in _ROOT_FILES:
            problems.append(
                f"{name!r}: not a file of a bundle's root ({', '.join(sorted(_ROOT_FILES))}, "
                f"{MANIFEST_DIRECTORY}/ and {ASSETS}/)"
            )
            continue
        problem = _type(status)
        if problem is not None:
            problems.append(f"{name}: {problem}")
    return problems


def load_bundle(root: Path) -> Bundle:
    """The bundle in the directory ``root`` (module docstring); ``BundleError`` with the
    problems found."""
    trust = _Trust()
    try:
        real, ways = swappable_ways(root)
    except (OSError, ValueError) as error:
        raise BundleError([f"{root}: not a directory that can be opened ({_why(error)})"]) from None
    for directory, why in ways:
        trust.way(str(root), why, directory)
    try:
        with _directory(str(real), None, str(root), trust) as descriptor:
            entries = _entries(descriptor, str(root))
            problems = _root_problems(entries)
            if problems:
                raise _LoadError("\n".join(problems))
            source = _manifest_bytes(descriptor, entries, trust)
            files = _files(descriptor, entries, trust)
    except _LoadError as problem_:
        raise BundleError([*str(problem_).split("\n"), *trust.lines(root, real)]) from None
    if trust.problems:
        raise BundleError(trust.lines(root, real))
    manifest = _manifest(source)
    if isinstance(manifest, str):
        raise BundleError([manifest])
    documents = _documents(manifest, files)
    if isinstance(documents, list):
        raise BundleError(documents)
    assets = {
        name: Asset(
            body=body,
            content_type=CONTENT_TYPES[os.path.splitext(name)[1]],
            immutable=name in documents.outputs and hashed(name, documents.outputs[name]),
        )
        for name, body in files.items()
    }
    return Bundle(MappingProxyType(assets), documents.catalogue, documents.operator)


# --- The routes -------------------------------------------------------------------------------


def _off_root(request: Request) -> Response | None:
    """The refusal of a request under any root path but the origin's, logged; ``None`` at the
    origin root."""
    found = classified(request.scope)
    if found is not None and found.root == "":
        return None
    _logger.error("The web bundle is served only at the origin root; this request has a root path")
    failed = refusal(RefusalCode.INTERNAL_ERROR, "The server failed")
    if found is not None and found.page:
        return refused_page(found.root or "", [failed], status=500)
    return refused([failed], status=500)


def _document(request: Request, body: bytes) -> Response:
    off = _off_root(request)
    if off is not None:
        return off
    return Response(body, headers=dict(DOCUMENT_HEADERS))


def bundle_router(bundle: Bundle) -> APIRouter:
    """``GET /``, ``GET /datasets/<dataset>``, ``GET /curate`` and ``GET /assets/<name>``
    (module docstring)."""
    router = APIRouter()

    @router.get(CATALOGUE_PATH, include_in_schema=False)
    async def catalogue(request: Request) -> Response:
        return _document(request, bundle.catalogue)

    @router.get(DATASETS_PREFIX + "/{dataset}", include_in_schema=False)
    async def dataset(request: Request, dataset: str) -> Response:
        return _document(request, bundle.catalogue)

    @router.get(CURATE_PATH, include_in_schema=False)
    async def curate(request: Request) -> Response:
        off = _off_root(request)
        if off is not None:
            return off
        if bundle.operator is None:
            raise HTTPException(status_code=404)
        return Response(bundle.operator, headers=dict(DOCUMENT_HEADERS))

    @router.get(ASSETS_PREFIX + "/{name}", include_in_schema=False)
    async def asset(request: Request, name: str) -> Response:
        off = _off_root(request)
        if off is not None:
            return off
        found = bundle.assets.get(name)
        if found is None:
            raise HTTPException(status_code=404)
        headers = {
            "Content-Type": found.content_type,
            "Cache-Control": IMMUTABLE if found.immutable else REVALIDATE,
        }
        return Response(found.body, headers=headers)

    return router


__all__ = [
    "ASSET_NAME",
    "CONTENT_TYPES",
    "DOCUMENT_HEADERS",
    "DOCUMENT_POLICY",
    "HASH",
    "IMMUTABLE",
    "MAX_BUNDLE_BYTES",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_LISTED",
    "REVALIDATE",
    "Asset",
    "Bundle",
    "BundleError",
    "bundle_router",
    "document",
    "hashed",
    "load_bundle",
]

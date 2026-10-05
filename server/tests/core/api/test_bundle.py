"""The web bundle (SPEC §12.4, §14; D410–D413): what the loader refuses, the documents the server
writes from a real Vite 8 manifest, the headers and types of what it serves, and where it serves
at all. The checked-in bundle (``fixtures/vite8``) is a real two-entry build's manifest and own
documents, with stand-ins for its scripts; ``fixtures/documents.json`` is the round 1 review's
corpus of documents a validator's parser and a browser read differently, which a bundle's own
``index.html`` may hold to no effect, since the server never reads it."""

import errno
import io
import json
import logging
import mimetypes
import os
import pwd
import re
import shutil
import signal
import stat
from collections.abc import Callable, Iterator
from html.parser import HTMLParser
from pathlib import Path
from types import FrameType, SimpleNamespace
from typing import Any, get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount

from aibi.core.api import bundle as bundle_module
from aibi.core.api import serve as serve_module
from aibi.core.api.app import create_app
from aibi.core.api.bundle import (
    DOCUMENT_POLICY,
    MAX_BUNDLE_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_LISTED,
    BundleError,
    bundle_router,
    hashed,
    load_bundle,
)
from aibi.core.api.config import BASE, ConfigError, ServerConfig, load_config, server_owns
from aibi.core.api.errors import install, status_of
from aibi.core.api.protection import (  # pyright: ignore[reportPrivateUsage]
    _LIMIT_NAMES,
    Policy,
    RateClass,
)
from aibi.core.api.serve import check, main, services_of, tools_of
from aibi.core.schema import limits
from aibi.core.schema.output import text
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode
from aibi.core.store.store import Store

Bundled = Any
MakeBundled = Callable[..., Bundled]
Write = Callable[..., Path]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CORPUS: dict[str, str] = json.loads((FIXTURES / "documents.json").read_text(encoding="utf-8"))
HASH = "sha256:" + "a" * 64
OWN = "http://127.0.0.1:8000"
SAME = {"Sec-Fetch-Site": "same-origin"}
DEFAULT = "default-src 'none'; frame-ancestors 'none'"
IMMUTABLE = "public, max-age=31536000, immutable"
MIB = 1024 * 1024


def problems(root: Path) -> list[str]:
    with pytest.raises(BundleError) as refused:
        load_bundle(root)
    return list(refused.value.problems)


def remedy_of(found: list[str]) -> tuple[list[str], list[str]]:
    """What a refusal says is wrong, and the commands it says to run (its header, and the lines
    after it, which are the refusal's last)."""
    headers = [
        index for index, line in enumerate(found) if line.endswith(": to fix it, run as root:")
    ]
    assert len(headers) <= 1, found
    if not headers:
        return found, []
    return found[: headers[0]], found[headers[0] + 1 :]


def manifest_of(root: Path) -> dict[str, Any]:
    found: dict[str, Any] = json.loads((root / ".vite" / "manifest.json").read_text())
    return found


def write_manifest(root: Path, manifest: dict[str, Any] | bytes) -> None:
    written = manifest if isinstance(manifest, bytes) else json.dumps(manifest).encode()
    (root / ".vite" / "manifest.json").write_bytes(written)


class Loads(HTMLParser):
    """The ``<script>`` and ``<link>`` elements of a document, in order, but an icon."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str, str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        given = dict(attrs)
        if tag == "script":
            self.found.append(
                ("script", given["type"] or "", given["src"] or "", "crossorigin" in given)
            )
        if tag == "link" and given.get("rel") != "icon":
            self.found.append(
                ("link", given["rel"] or "", given["href"] or "", "crossorigin" in given)
            )


def loads(document: bytes) -> list[tuple[str, str, str, bool]]:
    parsed = Loads()
    parsed.feed(document.decode("utf-8"))
    return parsed.found


# --- The documents (D411) --------------------------------------------------------------------


def test_the_documents_load_what_the_builds_own_documents_load(vite8: Path) -> None:
    bundle = load_bundle(vite8)
    assert bundle.operator is not None
    for served, own in ((bundle.catalogue, "index.html"), (bundle.operator, "operator.html")):
        written = loads((FIXTURES / "vite8" / own).read_bytes())
        assert loads(served) == written
        assert len(written) >= 4
        assert b"icon" not in served
    assert loads(bundle.catalogue)[-1] == ("link", "stylesheet", "/assets/lib-CQN2sVbY.css", True)
    assert bundle.catalogue == (
        b"<!doctype html>\n"
        b'<html lang="en">\n'
        b"<head>\n"
        b'<meta charset="utf-8">\n'
        b"<title>aibi</title>\n"
        b'<script type="module" crossorigin src="/assets/index-C4-4bqdv.js"></script>\n'
        b'<link rel="modulepreload" crossorigin href="/assets/jsx-runtime-BA8XPfTx.js">\n'
        b'<link rel="modulepreload" crossorigin href="/assets/lib-DSNbs8qM.js">\n'
        b'<link rel="stylesheet" crossorigin href="/assets/lib-CQN2sVbY.css">\n'
        b"</head>\n"
        b'<body><div id="root"></div></body>\n'
        b"</html>\n"
    )


def test_the_documents_name_what_the_manifest_names(bundle_dir: Path) -> None:
    """The entry's script is the manifest's ``file``, whatever the files are called, and the
    lazy chunks are left to the script that imports them."""
    shutil.move(
        bundle_dir / "assets" / "index-C4-4bqdv.js", bundle_dir / "assets" / "main-ZZZZZZZZ.js"
    )
    manifest = manifest_of(bundle_dir)
    manifest["index.html"]["file"] = "assets/main-ZZZZZZZZ.js"
    write_manifest(bundle_dir, manifest)
    bundle = load_bundle(bundle_dir)
    assert loads(bundle.catalogue)[0] == ("script", "module", "/assets/main-ZZZZZZZZ.js", True)
    assert b"charts-" not in bundle.catalogue
    assert b"session-" not in bundle.catalogue


def test_a_cycle_of_imports_ends_and_each_chunk_loads_once(bundle_dir: Path) -> None:
    manifest = manifest_of(bundle_dir)
    manifest["_jsx-runtime-BA8XPfTx.js"]["imports"] = ["_lib-DSNbs8qM.js", "index.html"]
    manifest["_jsx-runtime-BA8XPfTx.js"]["css"] = ["assets/session-odZNJXc1.css"]
    write_manifest(bundle_dir, manifest)
    found = loads(load_bundle(bundle_dir).catalogue)
    assert [url for _, _, url, _ in found] == [
        "/assets/index-C4-4bqdv.js",
        "/assets/lib-DSNbs8qM.js",
        "/assets/jsx-runtime-BA8XPfTx.js",
        "/assets/session-odZNJXc1.css",
        "/assets/lib-CQN2sVbY.css",
    ]


def test_a_chain_of_imports_longer_than_pythons_recursion_loads(bundle_dir: Path) -> None:
    manifest = manifest_of(bundle_dir)
    length = 1500
    for index in range(length):
        name = f"c{index}-AAAAAAAA.js"
        (bundle_dir / "assets" / name).write_bytes(b"")
        following = [f"_c{index + 1}"] if index + 1 < length else []
        manifest[f"_c{index}"] = {"file": f"assets/{name}", "imports": following}
    manifest["index.html"]["imports"] = ["_c0"]
    write_manifest(bundle_dir, manifest)
    found = [
        url for _, rel, url, _ in loads(load_bundle(bundle_dir).catalogue) if rel == "modulepreload"
    ]
    assert found == [f"/assets/c{index}-AAAAAAAA.js" for index in reversed(range(length))]


def test_unknown_manifest_members_are_ignored(bundle_dir: Path, vite8: Path) -> None:
    manifest = manifest_of(bundle_dir)
    for chunk in manifest.values():
        chunk["integrity"] = "sha384-x"
        chunk["names"] = ["x"]
    write_manifest(bundle_dir, manifest)
    assert load_bundle(bundle_dir).catalogue == load_bundle(vite8).catalogue


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (lambda m: m.pop("index.html"), "'index.html' is not an entry"),
        (lambda m: m["index.html"].update(isEntry=False), "'index.html' is not an entry"),
        (lambda m: m["operator.html"].pop("isEntry"), "'operator.html' is not an entry"),
        (lambda m: m["index.html"].update(file="public/index.js"), "not a file of assets/"),
        (lambda m: m["index.html"].update(file="/assets/index-C4-4bqdv.js"), "not a file of"),
        (lambda m: m["index.html"].update(file="assets/../index-C4-4bqdv.js"), "not a file of"),
        (lambda m: m["index.html"].update(file="assets/sub/index-C4-4bqdv.js"), "not a file of"),
        (lambda m: m["index.html"].update(file="assets/absent-AAAAAAAA.js"), "not a file of"),
        (lambda m: m["index.html"].update(file="assets/" + "a" * 126 + ".js"), "not a file of"),
        (lambda m: m["src/logo.png"].update(file="assets/logo.png"), "not a file of assets/"),
        (lambda m: m["index.html"].update(file="assets/icon-PHoxJkZi.svg"), "is not a script"),
        (lambda m: m["src/charts.tsx"].update(file="assets/lib-CQN2sVbY.css"), "is not a script"),
        (lambda m: m["_lib-DSNbs8qM.js"].update(file="assets/logo-BnV1MXKY.png"), "not a script"),
        (lambda m: m["_lib-DSNbs8qM.js"].update(css=["assets/lib-DSNbs8qM.js"]), "a stylesheet"),
        (lambda m: m["index.html"]["imports"].append("_gone-AAAAAAAA.js"), "does not hold"),
        (lambda m: m["index.html"]["dynamicImports"].append("src/gone.tsx"), "does not hold"),
        (lambda m: m["src/session.tsx"]["imports"].append("assets/cyca-B_qzb4UA.js"), "not hold"),
        (lambda m: m["index.html"].update(isEntry="yes"), "not a Vite manifest"),
        (lambda m: m["index.html"].update(imports="_lib-DSNbs8qM.js"), "not a Vite manifest"),
        (lambda m: m["index.html"].update(file=3), "not a Vite manifest"),
        (lambda m: m["index.html"].update(name=3), "not a Vite manifest"),
        (lambda m: m["index.html"].pop("file"), "not a Vite manifest"),
    ],
)
def test_a_manifest_that_names_what_the_bundle_does_not_hold_refuses_start(
    bundle_dir: Path, change: Callable[[dict[str, Any]], object], said: str
) -> None:
    manifest = manifest_of(bundle_dir)
    change(manifest)
    write_manifest(bundle_dir, manifest)
    found = problems(bundle_dir)
    assert any(said in problem for problem in found), found


@pytest.mark.parametrize(
    "written",
    [
        b'{"index.html": {"file": "assets/index-C4-4bqdv.js"}, "index.html": {"file": "x"}}',
        b'{"index.html": {"file": "assets/\xff.js", "isEntry": true}}',
        b'{"index.html": {"file": "assets/index-C4-4bqdv.js", "isEntry": true, "x": NaN}}',
        b'{"index.html": {"file": "assets/index-C4-4bqdv.js", "isEntry": true, "x": Infinity}}',
        b'\xef\xbb\xbf{"index.html": {"file": "assets/index-C4-4bqdv.js", "isEntry": true}}',
        b'["index.html"]',
        b"",
    ],
)
def test_a_manifest_that_is_not_strict_json_refuses_start(bundle_dir: Path, written: bytes) -> None:
    write_manifest(bundle_dir, written)
    assert any("manifest.json" in problem for problem in problems(bundle_dir))


def test_the_builds_own_documents_are_never_read(bundle_dir: Path, vite8: Path) -> None:
    """Whatever the build's ``index.html`` holds, the round 1 corpus included, the documents are
    the template's; a document too large or not UTF-8 is not even opened."""
    clean = load_bundle(vite8)
    for name, written in CORPUS.items():
        for own in ("index.html", "operator.html"):
            (bundle_dir / own).write_text(written, encoding="utf-8")
        bundle = load_bundle(bundle_dir)
        assert (bundle.catalogue, bundle.operator) == (clean.catalogue, clean.operator), name
    with open(bundle_dir / "index.html", "wb") as huge:
        huge.truncate(1024 * MIB)
    (bundle_dir / "operator.html").write_bytes(b"\xff\xfe<meta http-equiv=refresh>")
    assert load_bundle(bundle_dir).catalogue == clean.catalogue
    (bundle_dir / "index.html").unlink()
    (bundle_dir / "operator.html").unlink()
    assert load_bundle(bundle_dir).catalogue == clean.catalogue


# --- What the loader refuses (D410) ----------------------------------------------------------


def _symlinked_file(root: Path) -> None:
    os.symlink(root / "assets" / "lib-CQN2sVbY.css", root / "assets" / "linked-AAAAAAAA.css")


def _symlinked_assets(root: Path) -> None:
    shutil.move(root / "assets", root.parent / "elsewhere")
    os.symlink(root.parent / "elsewhere", root / "assets")


def _symlinked_vite(root: Path) -> None:
    shutil.move(root / ".vite", root.parent / "vite-elsewhere")
    os.symlink(root.parent / "vite-elsewhere", root / ".vite")


def _symlinked_manifest(root: Path) -> None:
    shutil.move(root / ".vite" / "manifest.json", root.parent / "manifest.json")
    os.symlink(root.parent / "manifest.json", root / ".vite" / "manifest.json")


def _symlinked_document(root: Path) -> None:
    (root / "index.html").unlink()
    os.symlink(root / "operator.html", root / "index.html")


def _fifo(root: Path) -> None:
    os.mkfifo(root / "assets" / "pipe-AAAAAAAA.js")


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (_symlinked_file, "linked-AAAAAAAA.css: a symbolic link"),
        (_symlinked_assets, "assets: a symbolic link"),
        (_symlinked_vite, ".vite: a symbolic link"),
        (_symlinked_manifest, "manifest.json: a symbolic link"),
        (_symlinked_document, "index.html: a symbolic link"),
        (_fifo, "pipe-AAAAAAAA.js: not a regular file"),
        (lambda r: (r / "assets" / "sub").mkdir(), "sub: a directory"),
        (lambda r: (r / "favicon.ico").write_bytes(b"x"), "'favicon.ico': not a file of"),
        (lambda r: (r / "public").mkdir(), "'public': not a file of"),
        (lambda r: (r / ".vite" / "ssr-manifest.json").write_bytes(b"{}"), "manifest.json alone"),
        (lambda r: (r / "assets" / "font-AAAAAAAA.woff2").write_bytes(b"x"), "not of a type"),
        (lambda r: (r / "assets" / "index-C4-4bqdv.js.map").write_bytes(b"{}"), "not of a type"),
        (lambda r: (r / "assets" / ("a" * 126 + ".js")).write_bytes(b""), "not a name"),
        (lambda r: (r / "assets" / "a..b.js").write_bytes(b""), "not a name"),
        (lambda r: (r / "assets" / ".hidden.js").write_bytes(b""), "not a name"),
        (lambda r: (r / "assets" / "-lib.js").write_bytes(b""), "not a name"),
        (lambda r: (r / "assets" / "a b.js").write_bytes(b""), "not a name"),
        (lambda r: shutil.rmtree(r / ".vite"), "manifest.json: missing"),
        (lambda r: shutil.rmtree(r / "assets"), "assets/: missing"),
    ],
)
def test_the_loader_refuses_anything_but_the_bundles_regular_files(
    bundle_dir: Path, change: Callable[[Path], object], said: str
) -> None:
    change(bundle_dir)
    found = problems(bundle_dir)
    assert any(said in problem for problem in found), found


def test_a_name_of_128_characters_is_one_a_file_may_have(bundle_dir: Path) -> None:
    (bundle_dir / "assets" / ("a" * 125 + ".js")).write_bytes(b"")
    assert ("a" * 125 + ".js") in load_bundle(bundle_dir).assets


def test_a_file_of_16_mib_loads_and_one_byte_more_does_not(bundle_dir: Path) -> None:
    big = bundle_dir / "assets" / "big-AAAAAAAA.png"
    big.write_bytes(b"\0" * MAX_FILE_BYTES)
    assert len(load_bundle(bundle_dir).assets["big-AAAAAAAA.png"].body) == MAX_FILE_BYTES
    with big.open("ab") as file:
        file.write(b"\0")
    assert any("big-AAAAAAAA.png: more than" in problem for problem in problems(bundle_dir))


def test_the_files_together_load_up_to_64_mib(bundle_dir: Path) -> None:
    assert MAX_BUNDLE_BYTES == 4 * MAX_FILE_BYTES
    present = sum(path.stat().st_size for path in (bundle_dir / "assets").iterdir())
    for index, size in enumerate((MAX_FILE_BYTES,) * 3 + (MAX_FILE_BYTES - present,)):
        (bundle_dir / "assets" / f"fill-{index}.png").write_bytes(b"\0" * size)
    assert load_bundle(bundle_dir).size == MAX_BUNDLE_BYTES
    (bundle_dir / "assets" / "more.png").write_bytes(b"\0")
    assert any("bytes together" in problem for problem in problems(bundle_dir))


def test_a_bundle_of_2000_files_loads_and_one_more_does_not(bundle_dir: Path) -> None:
    present = len(list((bundle_dir / "assets").iterdir()))
    for index in range(MAX_FILES - present):
        (bundle_dir / "assets" / f"f{index}.png").write_bytes(b"")
    assert load_bundle(bundle_dir).files == MAX_FILES
    (bundle_dir / "assets" / "one-more.png").write_bytes(b"")
    assert any("more than 2000 files" in problem for problem in problems(bundle_dir))


def test_a_bundle_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    assert "not a directory" in problems(tmp_path / "nowhere")[0]
    (tmp_path / "file").write_bytes(b"")
    assert "not a directory" in problems(tmp_path / "file")[0]


# --- The loader's own refusals, whatever the file system does (D410) ---------------------------


@pytest.fixture
def deadline() -> Iterator[None]:
    """A loader that waits on a pipe or loops would hang the suite; end it instead."""

    def expired(_signal: int, _frame: FrameType | None) -> None:
        raise TimeoutError("the loader did not return")

    before = signal.signal(signal.SIGALRM, expired)
    signal.alarm(20)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, before)


def _descriptors() -> int:
    return len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else 0


class _FailingRead:
    """A file whose read fails, as a disk does."""

    def __init__(self, file: Any) -> None:
        self.file = file

    def __enter__(self) -> "_FailingRead":
        self.file.__enter__()
        return self

    def __exit__(self, *given: Any) -> Any:
        return self.file.__exit__(*given)

    def read(self, size: int = -1) -> bytes:
        raise OSError(errno.EIO, "injected")


SITES = ("open", "listdir", "stat", "fstat", "fdopen", "close", "read")


def _inject(monkeypatch: pytest.MonkeyPatch, site: str, nth: int | None) -> list[int]:
    """Make the ``nth`` call of ``os.<site>`` (``read``: of a file's ``read``) fail as ``EIO``,
    and count the calls (every one when ``nth`` is ``None``)."""
    name = "fdopen" if site == "read" else site
    real = getattr(os, name)
    calls = [0]

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        failing = calls[0] == nth
        if failing and site == "close":
            real(*args, **kwargs)  # the descriptor is gone, whatever the error says
        if failing and site != "read":
            raise OSError(errno.EIO, "injected")
        found = real(*args, **kwargs)
        return _FailingRead(found) if failing else found

    monkeypatch.setattr(os, name, wrapper)
    return calls


@pytest.mark.parametrize("site", SITES)
def test_an_error_of_the_system_at_any_call_refuses_the_bundle_and_leaks_nothing(
    bundle_dir: Path, monkeypatch: pytest.MonkeyPatch, site: str
) -> None:
    """Start while a build rewrites the directory, a disk fails or a descriptor is refused: the
    loader's own refusal (exit 2 at the server), never a traceback, with no descriptor left."""
    with monkeypatch.context() as counting:
        calls = _inject(counting, site, None)
        load_bundle(bundle_dir)
    assert calls[0] >= 3, site
    before = _descriptors()
    for nth in range(1, calls[0] + 1):
        with monkeypatch.context() as failing:
            _inject(failing, site, nth)
            with pytest.raises(BundleError) as refused:
                load_bundle(bundle_dir)
        assert "injected" in "\n".join(refused.value.problems), (site, nth)
        assert _descriptors() == before, (site, nth)
    assert load_bundle(bundle_dir).files == 12


def test_a_close_that_fails_while_a_refusal_is_on_its_way_does_not_replace_it(
    bundle_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At every close after the first, a file or directory is held while a refusal is on its way
    (here a file over its cap, found in the ``assets/`` descriptor): the refusal stands. The first
    close is the manifest directory's, with no refusal yet, and is the refusal."""
    (bundle_dir / "assets" / "big-AAAAAAAA.png").write_bytes(b"\0" * (MAX_FILE_BYTES + 1))
    with monkeypatch.context() as counting:
        calls = _inject(counting, "close", None)
        refused = problems(bundle_dir)
    assert any("big-AAAAAAAA.png: more than" in problem for problem in refused), refused
    assert calls[0] >= 3
    for nth in range(1, calls[0] + 1):
        with monkeypatch.context() as failing:
            _inject(failing, "close", nth)
            found = problems(bundle_dir)
        if nth == 1:
            assert any("cannot be closed" in problem for problem in found), found
        else:
            assert any("big-AAAAAAAA.png: more than" in problem for problem in found), (nth, found)
            assert not any("cannot be closed" in problem for problem in found), (nth, found)


def test_a_close_that_fails_during_a_refusal_of_the_manifest_directory_does_not_replace_it(
    bundle_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (bundle_dir / ".vite" / "extra.json").write_bytes(b"{}")
    _inject(monkeypatch, "close", 1)
    found = problems(bundle_dir)
    assert any("holds manifest.json alone" in problem for problem in found), found
    assert not any("cannot be closed" in problem for problem in found), found


def _moved_aside(target: Path) -> Path:
    """Where the original is kept, as the thing a link now points to."""
    aside = target.parents[1] / "aside"
    aside.mkdir(exist_ok=True)
    kept = aside / target.name
    target.rename(kept)
    return kept


def _swap_symlink(target: Path) -> None:
    os.symlink(_moved_aside(target), target)


def _swap_fifo(target: Path) -> None:
    _moved_aside(target)
    os.mkfifo(target)


def _swap_directory(target: Path) -> None:
    _moved_aside(target)
    target.mkdir()


def _swap_nothing(target: Path) -> None:
    _moved_aside(target)


def _swap_writable(target: Path) -> None:
    os.chmod(target, 0o777 if target.is_dir() else 0o666)


SWAPS: dict[str, Callable[[Path], None]] = {
    "symlink": _swap_symlink,
    "fifo": _swap_fifo,
    "directory": _swap_directory,
    "nothing": _swap_nothing,
    "writable": _swap_writable,
}
LISTED = {
    "assets/index-C4-4bqdv.js": "assets/",
    ".vite/manifest.json": ".vite/",
    "assets": "ROOT",
    ".vite": "ROOT",
}


@pytest.mark.parametrize("swap", SWAPS)
@pytest.mark.parametrize("target", LISTED)
def test_an_entry_swapped_after_it_was_listed_refuses_the_bundle(
    bundle_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    deadline: None,
    target: str,
    swap: str,
) -> None:
    """The listing's ``lstat`` is stale by the time an entry is opened; the open's own flags and
    the checks on its descriptor are what hold. Each kind of entry replaced after the listing of
    its directory by a link, a pipe, a directory, nothing or a mode others can write: refused,
    without waiting on the pipe."""
    entries = bundle_module._entries  # pyright: ignore[reportPrivateUsage]
    swapped = [False]

    def listing(descriptor: int, where: str) -> dict[str, os.stat_result]:
        found = entries(descriptor, where)
        if where == LISTED[target].replace("ROOT", str(bundle_dir)) and not swapped[0]:
            swapped[0] = True
            SWAPS[swap](bundle_dir / target)
        return found

    monkeypatch.setattr(bundle_module, "_entries", listing)
    with pytest.raises(BundleError):
        load_bundle(bundle_dir)
    assert swapped[0]


def test_a_file_that_grows_past_its_cap_after_it_was_measured_refuses_the_bundle(
    bundle_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size on the descriptor is not what bounds the read: the file may have grown since."""
    (bundle_dir / "assets" / "big-AAAAAAAA.png").write_bytes(b"\0" * (MAX_FILE_BYTES + 1))
    fstat = os.fstat

    def small(descriptor: int) -> Any:
        found = fstat(descriptor)
        return SimpleNamespace(st_mode=found.st_mode, st_uid=found.st_uid, st_size=0)

    with monkeypatch.context() as measuring:
        measuring.setattr(os, "fstat", small)
        found = problems(bundle_dir)
    assert any("big-AAAAAAAA.png: more than" in problem for problem in found), found


def _linked(root: Path, copies: Path, names: int) -> None:
    """Give every file of the bundle ``names`` further names outside it, as an install by hard
    links (uv's default on Linux, Nix's store, ``cp -al``, ``rsync --link-dest``) does."""
    for here, _, files in os.walk(root):
        for name in files:
            for copy in range(names):
                target = copies / str(copy) / Path(here, name).relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(Path(here, name), target)


def test_a_bundle_installed_by_hard_links_loads(bundle_dir: Path, tmp_path: Path) -> None:
    clean = load_bundle(bundle_dir)
    _linked(bundle_dir, tmp_path / "cache", 2)
    assert (bundle_dir / "assets" / "index-C4-4bqdv.js").stat().st_nlink == 3
    assert (bundle_dir / ".vite" / "manifest.json").stat().st_nlink == 3
    assert (bundle_dir / "index.html").stat().st_nlink == 3
    linked = load_bundle(bundle_dir)
    assert linked.files == 12
    assert linked.catalogue == clean.catalogue
    assert linked.operator == clean.operator
    assert {name: found.body for name, found in linked.assets.items()} == {
        name: found.body for name, found in clean.assets.items()
    }


@pytest.mark.parametrize("bit", [stat.S_IWGRP, stat.S_IWOTH])
@pytest.mark.parametrize(
    "path", ["", ".vite", "assets", ".vite/manifest.json", "assets/index-C4-4bqdv.js"]
)
def test_a_file_or_directory_that_group_or_others_can_write_is_refused(
    bundle_dir: Path, path: str, bit: int
) -> None:
    target = bundle_dir / path
    os.chmod(target, stat.S_IMODE(target.stat().st_mode) | bit)
    found = problems(bundle_dir)
    listed, commands = remedy_of(found)
    assert any(
        "writable by group or others" in problem and Path(path).name in problem
        for problem in listed
    ), found
    assert len(listed) == 1, found
    assert commands == [f"chmod -R go-w {bundle_dir}"], found


@pytest.mark.parametrize("modes", [(0o700, 0o600), (0o750, 0o640), (0o755, 0o444), (0o555, 0o400)])
def test_a_bundle_that_others_may_read_but_not_write_loads(
    bundle_dir: Path, modes: tuple[int, int]
) -> None:
    directory, file = modes
    for here, _, names in os.walk(bundle_dir):
        for name in names:
            os.chmod(Path(here, name), file)
    for here, _, _ in os.walk(bundle_dir, topdown=False):
        os.chmod(here, directory)
    try:
        assert load_bundle(bundle_dir).files == 12
    finally:
        for here, _, _ in os.walk(bundle_dir):
            os.chmod(here, 0o755)


def test_the_builds_own_documents_are_checked_for_their_type_alone(
    bundle_dir: Path, tmp_path: Path
) -> None:
    """They are never read or served, so a mode or a second name on them protects nothing."""
    _linked(bundle_dir, tmp_path / "cache", 1)
    for name in ("index.html", "operator.html"):
        os.chmod(bundle_dir / name, 0o777)
    assert load_bundle(bundle_dir).files == 12
    (bundle_dir / "index.html").unlink()
    os.mkfifo(bundle_dir / "index.html")
    assert any("index.html: not a regular file" in problem for problem in problems(bundle_dir))


def test_every_problem_of_trust_is_refused_at_once_with_the_remedy_named_once(
    bundle_dir: Path,
) -> None:
    """A build made under umask 002 is 775/664 throughout: one refusal lists it, the first
    ``MAX_LISTED`` and how many more, and says how to fix it once."""
    for number in range(25):
        (bundle_dir / "assets" / f"extra{number:02}-AbCd1234.js").write_bytes(b"")
    for here, _, files in os.walk(bundle_dir):
        os.chmod(here, 0o775)
        for name in files:
            os.chmod(Path(here, name), 0o664)
    found = problems(bundle_dir)
    total = 1 + 1 + 1 + 1 + 12 + 25  # the root, .vite/, its manifest, assets/ and the files
    assert len(found) == MAX_LISTED + 3, found
    assert found[0] == f"{bundle_dir}: writable by group or others"
    assert found[1] == ".vite/: writable by group or others"
    assert found[2] == ".vite/manifest.json: writable by group or others"
    assert found[3] == "assets/: writable by group or others"
    assert found[MAX_LISTED - 1].startswith("assets/")
    assert found[MAX_LISTED] == f"... and {total - MAX_LISTED} more"
    assert found[-2] == f"{bundle_dir}: to fix it, run as root:"
    assert found[-1] == f"chmod -R go-w {bundle_dir}"
    assert sum("chmod" in line for line in found) == 1
    assert not any("chown" in line for line in found)


def test_a_file_that_is_refused_for_its_type_is_reported_with_the_modes_found_before(
    bundle_dir: Path,
) -> None:
    os.chmod(bundle_dir, 0o775)
    (bundle_dir / "favicon.ico").write_bytes(b"")
    found = problems(bundle_dir)
    assert any("'favicon.ico': not a file of" in problem for problem in found), found
    assert f"{bundle_dir}: writable by group or others" in found
    assert found[-2:] == [f"{bundle_dir}: to fix it, run as root:", f"chmod -R go-w {bundle_dir}"]


def test_the_remedy_quotes_a_path_with_a_space(tmp_path: Path, vite8: Path) -> None:
    spaced = tmp_path / "my bundle"
    vite8.rename(spaced)
    os.chmod(spaced, 0o777)
    assert problems(spaced)[-1] == f"chmod -R go-w '{spaced}'"


def _user() -> str:
    """The name the remedy gives the server's user: ``root`` for root, else the account's."""
    return "root" if os.geteuid() == 0 else pwd.getpwuid(os.geteuid()).pw_name


def test_a_file_or_directory_owned_by_another_user_is_refused(
    bundle_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner is the server's user or root (D253); the owner function says which."""
    monkeypatch.setattr(bundle_module, "server_owns", lambda uid: False)
    found = problems(bundle_dir)
    assert f"{bundle_dir}: owned by another user" in found
    assert ".vite/: owned by another user" in found
    assert ".vite/manifest.json: owned by another user" in found
    assert "assets/: owned by another user" in found
    assert "assets/operator-BB_pm7nd.js: owned by another user" in found
    assert not any("index.html" in problem for problem in found), found
    assert not any("chmod" in problem for problem in found), found
    assert sum("chown -R" in problem for problem in found) == 1, found
    assert found[-1] == f"chown -R -h {_user()} {bundle_dir}"


def test_a_bundle_both_writable_and_foreign_is_refused_with_both_remedies_once(
    bundle_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.chmod(bundle_dir / "assets", 0o777)
    monkeypatch.setattr(bundle_module, "server_owns", lambda uid: False)
    assert remedy_of(problems(bundle_dir))[1] == [
        f"chmod -R go-w {bundle_dir}",
        f"chown -R -h {_user()} {bundle_dir}",
    ]


@pytest.mark.skipif(os.geteuid() != 0, reason="giving a file to another user needs root")
def test_a_file_that_another_user_owns_is_the_one_refused(bundle_dir: Path) -> None:
    os.chown(bundle_dir / "assets" / "operator-BB_pm7nd.js", 4242, -1)
    found = problems(bundle_dir)
    assert found[0] == "assets/operator-BB_pm7nd.js: owned by another user", found
    assert remedy_of(found)[1] == [f"chown -R -h {_user()} {bundle_dir}"], found


def test_the_server_s_user_and_root_own_a_bundle() -> None:
    assert server_owns(os.geteuid())
    assert server_owns(0)
    assert not server_owns(os.geteuid() + 1 if os.geteuid() else 4242)


def test_a_bundle_in_a_directory_others_can_replace_is_refused(
    bundle_dir: Path, tmp_path: Path
) -> None:
    """A group member of a umask-002 checkout renames ``dist`` aside and puts a tree of their own,
    with modes the loader accepts, in its place (D253's rule for the way to the configuration)."""
    os.chmod(tmp_path, 0o775)
    found = problems(bundle_dir)
    assert found[0] == (
        f"{bundle_dir}: its group or others can write its directory, and so replace it ({tmp_path})"
    )
    assert remedy_of(found)[1] == [f"chmod go-w {tmp_path}"], found
    os.chmod(tmp_path, 0o1777)  # as /tmp: they may add entries but not rename another's
    assert load_bundle(bundle_dir).files == 12
    os.chmod(tmp_path, 0o700)


def test_a_bundle_reached_through_a_link_in_a_directory_others_can_write_is_refused(
    bundle_dir: Path, tmp_path: Path
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    os.symlink(bundle_dir, shared / "web")
    os.chmod(shared, 0o777)
    found = problems(shared / "web")
    assert found[0].startswith(f"{shared / 'web'}: a symbolic link on its way lies in a directory")
    os.chmod(shared, 0o1777)  # the sticky bit, and the link is the server's user's or root's
    assert load_bundle(shared / "web").files == 12


def test_a_bundle_that_is_a_link_to_the_current_build_loads(
    bundle_dir: Path, tmp_path: Path
) -> None:
    """An atomic deployment keeps ``dist -> dist-<date>``: the link is followed, once."""
    os.symlink(bundle_dir, tmp_path / "current")
    assert load_bundle(tmp_path / "current").files == 12
    os.symlink("current", tmp_path / "again")
    assert load_bundle(tmp_path / "again").files == 12


def test_a_path_that_changes_while_it_is_resolved_is_a_refusal(
    bundle_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.symlink(bundle_dir, tmp_path / "current")

    def raced(_path: Any) -> str:
        raise OSError(errno.EINVAL, "Invalid argument")

    monkeypatch.setattr(os, "readlink", raced)
    found = problems(tmp_path / "current")
    assert found == [
        f"{tmp_path / 'current'}: not a directory that can be opened (Invalid argument)"
    ]


def test_a_path_with_a_nul_is_a_refusal() -> None:
    assert "not a directory that can be opened" in problems(Path("web\0dist"))[0]


# --- The build's names (D411) ------------------------------------------------------------------


def _chunk(root: Path, key: str, name: str, file: str) -> None:
    """Add a script ``file`` to the bundle as a chunk the catalogue entry imports, the manifest
    giving it the ``name`` of its key, as Vite does."""
    (root / "assets" / file).write_bytes(b"export {};\n")
    manifest = manifest_of(root)
    manifest[key] = {"file": f"assets/{file}", "name": name}
    manifest["index.html"]["imports"].append(key)
    write_manifest(root, manifest)


# A file is hashed when the manifest's own name for it is followed by a hyphen and 8 characters of
# ``[A-Za-z0-9_-]``: not when its name merely looks as if it were.
HASHED = [
    ("index-C4-4bqdv.js", {"index"}, True),
    ("mod1623-nhulxpun.js", {"mod1623"}, True),  # a hash of lower-case letters alone
    ("lib-CQN2sVbY.css", {"lib"}, True),
    ("logo-BnV1MXKY.png", {"logo"}, True),
    ("icon-abcdefg0.svg", {"icon"}, True),
    ("_commonjsHelpers-AbCd1234.js", {"_commonjsHelpers"}, True),
    ("404-AbCd1234.js", {"404"}, True),
    ("a-b-AbCd1234.js", {"a-b"}, True),
    ("a-b-AbCd1234.js", {"x", "a-b"}, True),
    ("x-abcd-efg.js", {"x"}, True),
    ("survival-km-chart.js", {"survival-km-chart"}, False),
    ("use-km-chart.js", {"use-km-chart"}, False),
    ("data-Explorer.js", {"data-Explorer"}, False),
    ("vendor-ReactDOM.js", {"vendor-ReactDOM"}, False),
    ("vendor-reactdom.js", {"vendor-reactdom"}, False),
    ("chart-renderer.js", {"chart-renderer"}, False),
    ("index.js", {"index"}, False),
    ("index-Abcd.js", {"index"}, False),
    ("index-AbCd12345.js", {"index"}, False),
    ("index-C4-4bqdv.js.js", {"index"}, False),
    ("index-C4-4bqdv.js", {"other"}, False),
    ("index-C4-4bqdv.js", {"ind"}, False),
    ("index-C4-4bqdv.js", {"index-C4"}, False),
    ("index-C4-4bqdv.js", set(), False),
    ("a-b-AbCd1234.js", {"a"}, False),
    ("indexAbCd1234.js", {"index"}, False),
    ("indexXAbCd1234.js", {"index"}, False),
    ("index-AbCd 123.js", {"index"}, False),
]


@pytest.mark.parametrize(("name", "stems", "expected"), HASHED)
def test_a_name_is_hashed_when_it_is_a_manifest_name_and_a_hash(
    name: str, stems: set[str], expected: bool
) -> None:
    assert hashed(name, stems) is expected


def test_every_file_of_a_hashed_build_is_immutable(
    make_bundled: MakeBundled, bundle_dir: Path
) -> None:
    bundled = make_bundled(bundle_dir)
    assert bundled.bundle.files == 12
    for name, found in bundled.bundle.assets.items():
        assert found.immutable, name
        sent = bundled.client.get(f"/assets/{name}", headers=SAME).headers["cache-control"]
        assert sent == IMMUTABLE, name


def _unhashed(root: Path) -> list[str]:
    """Rebuild the bundle as a build without ``[hash]`` writes it (``[name].js``), and with the
    names of the review's corpus: kebab-case words whose last two make 8 characters, a PascalCase
    name of 8 characters, a name with a digit. Returns the files of the chunks."""
    text = (root / ".vite" / "manifest.json").read_text()
    for path in sorted((root / "assets").iterdir()):
        plain = re.sub(r"-[A-Za-z0-9_-]{8}(\.\w+)$", r"\1", path.name)
        path.rename(path.with_name(plain))
        text = text.replace(f"assets/{path.name}", f"assets/{plain}")
    (root / ".vite" / "manifest.json").write_text(text)
    names = ["survival-km-chart", "use-km-chart", "data-Explorer", "vendor-ReactDOM"]
    for name in names:
        _chunk(root, f"src/{name}.tsx", name, f"{name}.js")
    return [f"{name}.js" for name in names]


def test_a_build_without_hashes_is_never_served_immutable(
    make_bundled: MakeBundled, bundle_dir: Path
) -> None:
    """A browser that kept ``data-Explorer.js`` for a year would run it against the next build's
    documents: the names a build without ``[hash]`` gives revalidate, whatever they look like."""
    chunks = _unhashed(bundle_dir)
    bundled = make_bundled(bundle_dir)
    assert bundled.bundle.files == 12 + len(chunks)
    for name, found in bundled.bundle.assets.items():
        assert not found.immutable, name
        sent = bundled.client.get(f"/assets/{name}", headers=SAME).headers["cache-control"]
        assert sent == "no-cache", name
    assert {"index.js", "lib.css", "logo.png", "survival-km-chart.js"} <= set(bundled.bundle.assets)


def test_a_file_is_immutable_only_under_the_name_its_manifest_entry_gives(
    make_bundled: MakeBundled, bundle_dir: Path
) -> None:
    """The stem is the chunk's ``name`` for its script and its stylesheets, the key's file name's
    for an image, and nothing for a chunk without a name or an asset it only loads."""
    manifest = manifest_of(bundle_dir)
    manifest["index.html"]["name"] = "home"  # its file is index-<hash>.js
    manifest["src/logo.png"]["file"] = "assets/logo-BnV1MXKY.png"
    manifest["src/session.tsx"].pop("name")  # session-<hash>.js and its stylesheet
    manifest["src/icon.svg"]["file"] = "assets/icon-PHoxJkZi.svg"
    (bundle_dir / "assets" / "operator-AbCd1234.png").write_bytes(b"x")
    manifest["operator.html"]["assets"].append(
        "assets/operator-AbCd1234.png"
    )  # no entry of its own
    write_manifest(bundle_dir, manifest)
    found = load_bundle(bundle_dir).assets
    assert not found["operator-AbCd1234.png"].immutable  # a chunk's name is not its asset's stem
    assert not found["index-C4-4bqdv.js"].immutable  # named ``home``, so it is not ``index``
    assert not found["session-DDexETaB.js"].immutable  # no name, so no stem
    assert not found["session-odZNJXc1.css"].immutable
    assert found["logo-BnV1MXKY.png"].immutable  # the stem of ``src/logo.png``
    assert found["icon-PHoxJkZi.svg"].immutable
    assert found["lib-CQN2sVbY.css"].immutable  # the stem is its chunk's name, ``lib``
    assert found["operator-BB_pm7nd.js"].immutable


def test_a_name_may_begin_with_an_underscore_or_a_digit(bundle_dir: Path) -> None:
    """As Rollup's chunks may (``_commonjsHelpers-<hash>.js``, ``404-<hash>.js`` from a module
    ``404.tsx``), though never with ``.`` or ``-``."""
    for stem in ("_commonjsHelpers", "404"):
        file = f"{stem}-AbCd1234.js"
        _chunk(bundle_dir, f"{stem}-AbCd1234.js", stem, file)
        bundle = load_bundle(bundle_dir)
        assert bundle.assets[file].immutable
        assert ("link", "modulepreload", f"/assets/{file}", True) in loads(bundle.catalogue)
    for refused in ("-lib.js", ".lib.js", "_.._lib.js"):
        (bundle_dir / "assets" / refused).write_bytes(b"")
        assert any("not a name" in problem for problem in problems(bundle_dir)), refused
        (bundle_dir / "assets" / refused).unlink()


def _minimal(**server: str) -> str:
    lines = "\n".join(f"{key} = {value}" for key, value in server.items())
    return (
        f'[server]\n{lines}\n[curator]\ntoken_hash = "{HASH}"\n'
        '[storage]\ndata = "data"\nimports = ["imports"]\n'
    )


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ('"data"', "inside the data directory"),
        ('"data/web"', "inside the data directory"),
        ('"data/uploads"', "inside the data directory"),
        ('"."', "inside the data directory"),
        ('"imports"', "inside storage.imports[0]"),
        ('"imports/web"', "inside storage.imports[0]"),
        ('"nowhere"', "not an existing directory"),
        ('"aibi.toml"', "not an existing directory"),
    ],
)
def test_a_bundle_where_imports_or_uploads_are_written_is_refused(
    write_config: Write, tmp_path: Path, given: str, said: str
) -> None:
    for directory in ("data/web", "data/uploads", "imports/web"):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    with pytest.raises(ConfigError) as refused:
        load_config(write_config(_minimal(web_bundle=given)))
    assert any(
        problem.startswith("server.web_bundle: ") and said in problem
        for problem in refused.value.problems
    ), refused.value.problems


@pytest.mark.parametrize(
    ("bundle", "said"),
    [
        ("web-data", "inside the data directory"),
        ("web-imports", "inside storage.imports[0]"),
        ("web-inside-imports", "inside storage.imports[0]"),
        ("web-above", "inside the data directory"),
    ],
)
def test_a_bundle_that_is_a_link_to_where_imports_or_uploads_are_written_is_refused(
    write_config: Write, tmp_path: Path, bundle: str, said: str
) -> None:
    """The comparison is by real paths: a link whose own path is beside the data directory is
    still the directory it points to."""
    for directory in ("data", "imports/web"):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    os.symlink(tmp_path / "data", tmp_path / "web-data")
    os.symlink(tmp_path / "imports", tmp_path / "web-imports")
    os.symlink(tmp_path / "imports" / "web", tmp_path / "web-inside-imports")
    os.symlink(tmp_path, tmp_path / "web-above")
    with pytest.raises(ConfigError) as refused:
        load_config(write_config(_minimal(web_bundle=f'"{bundle}"')))
    assert any(
        problem.startswith("server.web_bundle: ") and said in problem
        for problem in refused.value.problems
    ), refused.value.problems


def test_a_data_directory_that_is_a_link_to_the_bundles_parent_is_refused(
    write_config: Write, tmp_path: Path
) -> None:
    (tmp_path / "store" / "web").mkdir(parents=True)
    os.symlink(tmp_path / "store", tmp_path / "data")
    with pytest.raises(ConfigError) as refused:
        load_config(write_config(_minimal(web_bundle='"store/web"')))
    assert any(
        problem.startswith("server.web_bundle: ") and "inside the data directory" in problem
        for problem in refused.value.problems
    ), refused.value.problems


def test_a_bundle_that_holds_an_import_directory_is_refused(
    write_config: Write, tmp_path: Path
) -> None:
    """An import directory inside the bundle is as unsafe as the bundle inside one."""
    (tmp_path / "web" / "assets").mkdir(parents=True)
    text = _minimal(web_bundle='"web"').replace(
        'imports = ["imports"]', 'imports = ["imports", "web/assets"]'
    )
    with pytest.raises(ConfigError) as refused:
        load_config(write_config(text))
    assert any(
        problem.startswith("server.web_bundle: ") and "inside storage.imports[1]" in problem
        for problem in refused.value.problems
    ), refused.value.problems


def test_a_bundle_beside_the_data_loads_in_the_configuration(
    write_config: Write, tmp_path: Path, vite8: Path
) -> None:
    vite8.rename(tmp_path / "web")
    config = load_config(write_config(_minimal(web_bundle='"web"')))
    assert config.server.web_bundle == tmp_path / "web"


# --- Serving (D410, D411) ----------------------------------------------------------------------


def test_the_documents_and_files_carry_their_headers_exactly(
    make_bundled: MakeBundled, bundle_dir: Path
) -> None:
    (bundle_dir / "assets" / "react-router-dom.js").write_bytes(b"export {};\n")
    (bundle_dir / "assets" / "chart-renderer.js").write_bytes(b"export {};\n")
    bundled = make_bundled(bundle_dir)
    assert DOCUMENT_POLICY == (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
        "require-trusted-types-for 'script'; trusted-types 'none'"
    )
    for path, body in (
        ("/", bundled.bundle.catalogue),
        ("/datasets/orchard", bundled.bundle.catalogue),
        ("/curate", bundled.bundle.operator),
    ):
        answered = bundled.client.get(path, headers=SAME)
        assert answered.status_code == 200, path
        assert answered.content == body
        assert answered.headers["content-type"] == "text/html; charset=utf-8"
        assert answered.headers["content-security-policy"] == DOCUMENT_POLICY
        assert answered.headers["x-frame-options"] == "DENY"
        assert answered.headers["cache-control"] == "no-store"
        assert answered.headers["cross-origin-opener-policy"] == "same-origin"
        assert answered.headers["x-content-type-options"] == "nosniff"
        assert "etag" not in answered.headers
        assert "last-modified" not in answered.headers
    types = {
        "index-C4-4bqdv.js": "text/javascript; charset=utf-8",
        "lib-CQN2sVbY.css": "text/css; charset=utf-8",
        "icon-PHoxJkZi.svg": "image/svg+xml",
        "logo-BnV1MXKY.png": "image/png",
        "react-router-dom.js": "text/javascript; charset=utf-8",
        "chart-renderer.js": "text/javascript; charset=utf-8",
    }
    for name, kind in types.items():
        answered = bundled.client.get(f"/assets/{name}", headers=SAME)
        assert answered.status_code == 200, name
        assert answered.content == (bundle_dir / "assets" / name).read_bytes()
        assert answered.headers["content-type"] == kind
        assert answered.headers["content-security-policy"] == DEFAULT
        unhashed = name in ("react-router-dom.js", "chart-renderer.js")
        assert answered.headers["cache-control"] == ("no-cache" if unhashed else IMMUTABLE)
        assert "x-frame-options" not in answered.headers
        assert "etag" not in answered.headers
        assert "last-modified" not in answered.headers


def test_immutable_is_only_for_hashed_names_the_manifest_gives(
    make_bundled: MakeBundled, bundle_dir: Path
) -> None:
    shutil.move(bundle_dir / "assets" / "index-C4-4bqdv.js", bundle_dir / "assets" / "index.js")
    manifest = manifest_of(bundle_dir)
    manifest["index.html"]["file"] = "assets/index.js"
    write_manifest(bundle_dir, manifest)
    bundle = load_bundle(bundle_dir)
    assert not bundle.assets["index.js"].immutable
    assert bundle.assets["lib-DSNbs8qM.js"].immutable
    bundled = make_bundled(bundle_dir)
    assert bundled.client.get("/assets/index.js", headers=SAME).headers["cache-control"] == (
        "no-cache"
    )


def test_the_types_come_from_the_servers_table_never_mimetypes(
    make_bundled: MakeBundled, bundle_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong = dict.fromkeys((".js", ".css", ".svg", ".png", ".html"), "application/x-wrong")
    monkeypatch.setattr(mimetypes, "types_map", wrong)
    monkeypatch.setattr(mimetypes, "guess_type", lambda *a, **k: ("application/x-wrong", None))
    monkeypatch.setattr(
        mimetypes, "guess_file_type", lambda *a, **k: ("application/x-wrong", None), raising=False
    )
    monkeypatch.setattr(mimetypes, "inited", True)
    bundled = make_bundled(bundle_dir)
    for name, kind in (
        ("index-C4-4bqdv.js", "text/javascript; charset=utf-8"),
        ("lib-CQN2sVbY.css", "text/css; charset=utf-8"),
        ("icon-PHoxJkZi.svg", "image/svg+xml"),
        ("bg-CvUIKjNr.png", "image/png"),
    ):
        assert bundled.client.get(f"/assets/{name}", headers=SAME).headers["content-type"] == kind
    assert bundled.client.get("/", headers=SAME).headers["content-type"] == (
        "text/html; charset=utf-8"
    )


@pytest.mark.parametrize(
    ("path", "raw"),
    [
        ("/assets/../index.html", b"/assets/..%2findex.html"),
        ("/assets/..", b"/assets/%2e%2e"),
        ("/assets/.", b"/assets/%2e"),
        ("/assets/index-C4-4bqdv.js\x00", b"/assets/index-C4-4bqdv.js%00"),
        ("/assets/..\\.vite\\manifest.json", b"/assets/..%5c.vite%5cmanifest.json"),
        ("/assets/" + "a" * 129 + ".js", None),
        ("/assets/INDEX-C4-4BQDV.JS", None),
        ("/assets/index-C4-4bqdv.js/", None),
        ("/assets//index-C4-4bqdv.js", None),
        ("/assets/manifest.json", None),
        ("/assets/.vite", None),
        ("/.vite/manifest.json", None),
        ("/index.html", None),
        ("/operator.html", None),
    ],
)
def test_a_name_the_bundle_does_not_hold_is_not_found(
    make_bundled: MakeBundled,
    bundle_dir: Path,
    drive: Callable[..., list[Any]],
    sent: Any,
    path: str,
    raw: bytes | None,
) -> None:
    bundled = make_bundled(bundle_dir)
    headers = ((b"host", b"127.0.0.1:8000"), (b"sec-fetch-site", b"same-origin"))
    (got,) = drive(bundled.app, [sent(path=path, raw_path=raw, headers=headers)])
    assert got.status == 404, (path, got.body)
    assert got.header(b"content-type") == b"application/json"
    assert json.loads(got.body)["refusals"][0]["code"] == "NOT_FOUND"
    if path.startswith("/assets"):
        assert got.header(b"cache-control") == b"no-store"


def test_head_is_not_answered_and_any_query_is_ignored(
    make_bundled: MakeBundled, bundle_dir: Path
) -> None:
    bundled = make_bundled(bundle_dir)
    for path in ("/", "/datasets/x", "/curate", "/assets/index-C4-4bqdv.js"):
        answered = bundled.client.head(path, headers=SAME)
        assert answered.status_code == 405, path
        assert answered.headers["allow"] == "GET"
    for path in ("/?offset=x&offset=1", "/datasets/x?columns_offset=nine&other=1", "/curate?x=1"):
        answered = bundled.client.get(path, headers=SAME)
        assert answered.status_code == 200, path
        assert answered.headers["content-security-policy"] == DOCUMENT_POLICY
    assert bundled.client.get("/assets/index-C4-4bqdv.js?v=1", headers=SAME).status_code == 200


def test_without_an_operator_entry_curate_is_not_found(
    make_bundled: MakeBundled, bundle_dir: Path
) -> None:
    manifest = manifest_of(bundle_dir)
    del manifest["operator.html"]
    write_manifest(bundle_dir, manifest)
    bundled = make_bundled(bundle_dir)
    assert bundled.bundle.operator is None
    answered = bundled.client.get("/curate", headers=SAME)
    assert answered.status_code == 404
    assert answered.headers["content-type"] == "text/html; charset=utf-8"
    assert bundled.client.get("/", headers=SAME).status_code == 200


def test_without_a_bundle_the_pages_stay_and_its_paths_are_not_found(
    make_bundled: MakeBundled,
) -> None:
    bundled = make_bundled(None)
    catalogue = bundled.client.get("/")
    assert catalogue.status_code == 200
    assert "style-src 'sha256-" in catalogue.headers["content-security-policy"]
    curate = bundled.client.get("/curate", headers=SAME)
    assert (curate.status_code, curate.headers["content-type"]) == (404, "text/html; charset=utf-8")
    asset = bundled.client.get("/assets/index-C4-4bqdv.js", headers=SAME)
    assert (asset.status_code, asset.headers["content-type"]) == (404, "application/json")
    assert asset.headers["cache-control"] == "no-store"


def test_under_a_root_path_or_a_mount_every_bundle_route_refuses_loudly(
    make_bundled: MakeBundled, bundle_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bundled = make_bundled(bundle_dir)
    paths = ("/", "/datasets/x", "/curate", "/assets/index-C4-4bqdv.js", "/assets/gone.js")
    with TestClient(bundled.app, base_url=OWN, root_path="/aibi") as client:
        for path in paths:
            caplog.clear()
            with caplog.at_level(logging.ERROR, logger="aibi.core.api.bundle"):
                answered = client.get("/aibi" + path, headers=SAME)
            assert answered.status_code == 500, path
            assert "INTERNAL_ERROR" in answered.text
            assert b"/assets/" not in answered.content
            assert "served only at the origin root" in caplog.text
            page = path in ("/", "/datasets/x", "/curate")
            assert answered.headers["content-type"].startswith(
                "text/html" if page else "application/json"
            )
            if page:
                assert 'href="/aibi/"' in answered.text
    outer = Starlette(routes=[Mount("/m", app=bundled.app)])
    with TestClient(outer, base_url=OWN) as client:
        for path in paths:
            assert client.get("/m" + path, headers=SAME).status_code == 500, path
    with TestClient(bundled.app, base_url=OWN, root_path="/x/") as client:
        for path in paths:
            answered = client.get(path, headers=SAME)
            assert answered.status_code == 500, path


def test_no_secret_reaches_a_served_files_headers(
    make_bundled: MakeBundled, bundle_dir: Path
) -> None:
    bundled = make_bundled(bundle_dir)
    csrf = bundled.client.get("/operator/csrf", headers=bundled.headers(Origin=OWN)).json()["csrf"]
    handle = "ses_" + "A" * 43
    sent = bundled.headers(**SAME, **{"Aibi-CSRF": csrf, "Cookie": f"h={handle}"})
    paths = ["/", "/datasets/x", "/curate", "/curate/x", "/favicon.ico"]
    paths += [f"/assets/{name}" for name in bundled.bundle.assets] + ["/assets/gone.js"]
    for path in paths:
        answered = bundled.client.get(path, headers=sent)
        written = " ".join(f"{k}: {v}" for k, v in answered.headers.items()) + answered.text
        for secret in (bundled.token, csrf, handle, csrf[:20]):
            assert secret not in written, path
        assert "set-cookie" not in answered.headers


# --- The application and the server (D410) ---------------------------------------------------


def _parts(tmp_path: Path) -> tuple[ServerConfig, Store, Any]:
    (tmp_path / "imports").mkdir(exist_ok=True)
    config = ServerConfig.model_validate(
        {"curator": {"token_hash": HASH}, "storage": {"data": "data", "imports": ["imports"]}},
        context={BASE: tmp_path},
    )
    store = Store(config.storage.data)
    return config, store, services_of(config, store, PackRegistry((), core_version="0.0.1"))


async def _nothing(scope: Any, receive: Any, send: Any) -> None:
    raise AssertionError("never called")  # pragma: no cover


def test_a_bundle_needs_the_tool_calls_and_no_mount_takes_its_paths(
    tmp_path: Path, vite8: Path
) -> None:
    config, store, services = _parts(tmp_path)
    try:
        policy = Policy.of(config, csrf_key=b"k" * 32)
        bundle = load_bundle(vite8)
        with pytest.raises(ValueError, match="with the tool calls"):
            create_app(policy, services, bundle=bundle)
        tools = tools_of(config, store, None)
        options: list[dict[str, Any]] = [{}, {"tools": tools}, {"tools": tools, "bundle": bundle}]
        for given in options:
            for path in ("/assets", "/assets/x", "/curate", "/curate/x"):
                with pytest.raises(ValueError, match="under a path the application serves"):
                    create_app(policy, services, mounts={path: _nothing}, **given)
        create_app(policy, services, mounts={"/assetsx": _nothing, "/curator": _nothing})
    finally:
        store.close()


def _run_main(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), environ={}, stdin=io.StringIO(""), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_check_prints_the_bundle_and_a_refused_bundle_stops_the_server(
    write_config: Write, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vite8: Path
) -> None:
    vite8.rename(tmp_path / "web")
    path = write_config(_minimal(web_bundle='"web"'))
    code, out, _ = _run_main("check", "--config", str(path))
    assert code == 0
    size = sum(p.stat().st_size for p in (tmp_path / "web" / "assets").iterdir())
    assert (
        f"web bundle: {tmp_path / 'web'}, 12 files, {size} bytes; operator entry at /curate: yes\n"
        in out
    )
    assert "rate, assets: 600 a minute, bursts of 100\n" in out
    other = write_config(_minimal(port="8000"), name="other.toml")
    code, out, _ = _run_main("check", "--config", str(other))
    assert "web bundle: none (the catalogue page at / and /datasets/<id>)\n" in out
    (tmp_path / "web" / "assets" / "x.map").write_bytes(b"{}")
    (tmp_path / "web" / "extra.txt").write_bytes(b"")
    for command in ("check", "serve"):
        monkeypatch.setattr(serve_module, "Store", None)  # serve stops before the store opens
        code, out, err = _run_main(command, "--config", str(path))
        assert code == 2, command
        packs = "aibi-server: packs: none\n" if command == "serve" else ""
        assert err.startswith(packs + "aibi-server: the web bundle is refused:\n"), err
        assert "'extra.txt'" in err
        assert not (tmp_path / "data").exists()


def test_the_check_names_every_rate(tmp_path: Path) -> None:
    config, store, _ = _parts(tmp_path)
    store.close()
    printed = "\n".join(check(config))
    for name in ("operator", "api", "token failures", "proposals", "page", "assets"):
        assert f"rate, {name}: " in printed


# --- Every rate class lives in every place (D413) ----------------------------------------------


def test_every_rate_class_has_a_rate_a_limit_name_a_429_and_a_line_in_the_check(
    tmp_path: Path,
) -> None:
    config, store, _ = _parts(tmp_path)
    store.close()
    policy = Policy.of(config, csrf_key=b"k" * 32)
    printed = "\n".join(check(config))
    exported = {getattr(limits, name) for name in limits.__all__ if name.isupper()}
    classes = get_args(RateClass)
    assert set(classes) == {"operator", "api", "token_failures", "page", "assets"}
    for name in classes:
        assert name in policy.rates, name
        configured = getattr(config.server.rates, name)
        assert (policy.rates[name].per_minute, policy.rates[name].burst) == (
            configured.per_minute,
            configured.burst,
        )
        limit = _LIMIT_NAMES[name]
        assert limit in exported, name
        refusal = Refusal(
            code=RefusalCode.LIMIT_EXCEEDED,
            path=None,
            message=[text("x")],
            alternatives=[],
            limit=Limit(name=limit, max=1),
        )
        assert status_of(refusal) == 429, name
        assert f"rate, {name.replace('_', ' ')}: " in printed, name


# --- The operator entry's admission (D412) -----------------------------------------------------


def test_curate_admits_one_sec_fetch_site_of_none_or_same_origin_alone(
    make_bundled: MakeBundled,
    bundle_dir: Path,
    drive: Callable[..., list[Any]],
    sent: Any,
) -> None:
    one = {"per_minute": 1, "burst": 1}
    bundled = make_bundled(bundle_dir, server={"rates": {"page": one}})
    host = (b"host", b"127.0.0.1:8000")
    refused = [
        (),
        ((b"sec-fetch-site", b"none"), (b"sec-fetch-site", b"none")),
        ((b"sec-fetch-site", b"none"), (b"sec-fetch-site", b"cross-site")),
        ((b"sec-fetch-site", b"same-origin"), (b"sec-fetch-site", b"same-origin")),
        ((b"sec-fetch-site", b"None"),),
        ((b"sec-fetch-site", b" none"),),
        ((b"sec-fetch-site", b"same-origin "),),
        ((b"sec-fetch-site", b"none, same-origin"),),
        ((b"sec-fetch-site", b"cross-site, none"),),
        ((b"sec-fetch-site", b"same-site"),),
        ((b"sec-fetch-site", b""),),
        ((b"origin", OWN.encode()), (b"access-control-request-method", b"GET")),
    ]
    for path in ("/curate", "/curate/x"):
        for index, headers in enumerate(refused):
            for method in ("GET", "OPTIONS", "POST"):
                (got,) = drive(
                    bundled.app,
                    [
                        sent(
                            path=path,
                            method=method,
                            headers=(host, *headers),
                            client=(f"10.9.{index}.1", 1),
                        )
                    ],
                )
                assert got.status == 403, (path, headers, method)
                assert got.header(b"content-type") == b"text/html; charset=utf-8"
                assert b"ORIGIN_NOT_ALLOWED" in got.body
    for index, site in enumerate((b"none", b"same-origin")):
        client = (f"10.8.{index}.1", 1)
        (got,) = drive(
            bundled.app,
            [sent(path="/curate", headers=(host, (b"sec-fetch-site", site)), client=client)],
        )
        assert got.status == 200, site
    (fresh,) = drive(
        bundled.app,
        [sent(path="/", headers=(host, (b"sec-fetch-site", b"none")), client=("10.9.0.1", 1))],
    )
    assert fresh.status == 200, "a refused request at /curate spent the page rate"


def test_without_request_protections_record_the_bundle_serves_nothing(vite8: Path) -> None:
    """The routes fail closed outside ``create_app``: no record, no root path known."""
    app = FastAPI()
    install(app, pages=True)
    app.include_router(bundle_router(load_bundle(vite8)))
    with TestClient(app, base_url=OWN) as client:
        for path in ("/", "/datasets/x", "/curate", "/assets/index-C4-4bqdv.js"):
            answered = client.get(path)
            assert answered.status_code == 500, path
            assert answered.headers["content-type"] == "application/json"

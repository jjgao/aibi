"""The web bundle's trust rule and its remedy, by brute force (SPEC D253, D410).

- Each object the loader checks (the root, ``.vite/``, ``assets/``, each file read) under each
  fault (group-writable, other-writable, sticky and writable, owned by another user), once on
  the disk and once on the descriptor alone, so that a check made by name instead is caught, is
  refused as exactly that.
- The way to the root: the directory it lies in, and a link on it, under each fault.
- The refusal's bound, at ``MAX_LISTED`` less one, at it and above it.
- The remedy the refusal prints, for each layout a bundle is installed in (a plain root, a root
  that is a link to the current build, a checkout under umask 002, files of another user, a
  directory others can write, a link in one): its commands are run (``chmod`` really, in
  ``tmp_path``; ``chown`` as the owner helper models it, never through a link) and the bundle
  must then load.
- Every text of a hostile bundle (names, manifest keys and values) in a refusal is printable.

The ownership of a file is not something a test can set unless it is root, so ``Owners`` keeps
it off the disk and answers ``stat`` with it."""

import json
import os
import pwd
import shlex
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from aibi.core.api.bundle import MAX_LISTED, BundleError, load_bundle

HEADER = ": to fix it, run as root:"
LSTAT = os.lstat
FOREIGN = 4242 if os.geteuid() != 4242 else 4243
Key = tuple[int, int]
Copy = Callable[[Path, Path], Path]


def problems(root: Path) -> list[str]:
    with pytest.raises(BundleError) as refused:
        load_bundle(root)
    return list(refused.value.problems)


def remedy_of(found: list[str]) -> tuple[list[str], list[str]]:
    """What a refusal says is wrong, and the commands it says to run, which are its last lines
    after its header."""
    headers = [index for index, line in enumerate(found) if line.endswith(HEADER)]
    assert len(headers) <= 1, found
    if not headers:
        return found, []
    return found[: headers[0]], found[headers[0] + 1 :]


def server_user() -> str:
    return "root" if os.geteuid() == 0 else pwd.getpwuid(os.geteuid()).pw_name


class Owners:
    """The owner and the mode ``stat`` answers for an inode, as a test says. ``calls`` are the
    functions of ``os`` that answer so: ``fstat`` alone leaves what is read by name as the disk
    has it."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, calls: tuple[str, ...] = ("stat", "lstat", "fstat")
    ) -> None:
        self.uids: dict[Key, int] = {}
        self.modes: dict[Key, int] = {}
        for name in calls:
            monkeypatch.setattr(os, name, self._answering(getattr(os, name)))

    def _answering(self, real: Callable[..., os.stat_result]) -> Callable[..., os.stat_result]:
        def wrapper(*args: Any, **kwargs: Any) -> os.stat_result:
            found = real(*args, **kwargs)
            key = (found.st_dev, found.st_ino)
            uid = self.uids.get(key, found.st_uid)
            mode = self.modes.get(key, found.st_mode)
            if (uid, mode) == (found.st_uid, found.st_mode):
                return found
            values = list(found)  # st_mode, st_ino, st_dev, st_nlink, st_uid, ...
            values[0], values[4] = mode, uid
            return os.stat_result(values)

        return wrapper

    @staticmethod
    def key(path: Path) -> Key:
        found = LSTAT(path)
        return (found.st_dev, found.st_ino)

    def give(self, path: Path, uid: int) -> None:
        self.uids[self.key(path)] = uid

    def chown(self, args: list[str]) -> None:
        """``chown -R -h <user> <path>``: the path itself, and below it everything, never
        through a link (a link is changed, not what it points to)."""
        assert args[:3] == ["chown", "-R", "-h"], args
        assert len(args) == 5, args
        assert args[3] == server_user(), args
        target = Path(args[4])
        uid = os.geteuid()
        self.give(target, uid)
        if not stat.S_ISLNK(LSTAT(target).st_mode):
            for here, names, files in os.walk(target):
                for name in (*names, *files):
                    self.give(Path(here, name), uid)


BITS = {
    "group-writable": 0o020,
    "other-writable": 0o002,
    "sticky and group-writable": 0o1020,
    "sticky and other-writable": 0o1002,
}
FAULTS = (*BITS, "foreign owner")


def damage(path: Path, fault: str, owners: Owners, *, disk: bool) -> None:
    """Give ``path`` the ``fault``: on the disk, or in what ``fstat`` answers alone."""
    if fault == "foreign owner":
        owners.give(path, FOREIGN)
        return
    found = LSTAT(path)
    mode = stat.S_IMODE(found.st_mode) | BITS[fault]
    if disk:
        os.chmod(path, mode)
    else:
        owners.modes[Owners.key(path)] = stat.S_IFMT(found.st_mode) | mode


def refused_as(fault: str, label: str, real: Path) -> tuple[list[str], list[str]]:
    """The one problem a refusal lists for ``fault``, and the one command it prints."""
    if fault == "foreign owner":
        return [f"{label}: owned by another user"], [f"chown -R -h {server_user()} {real}"]
    return [f"{label}: writable by group or others"], [f"chmod -R go-w {real}"]


# --- Each object under each fault ---------------------------------------------------------------

DIRECTORIES = ("", ".vite", "assets")
FILES = (".vite/manifest.json", "assets/*")
OBJECTS = [(target, fault) for target in DIRECTORIES for fault in FAULTS] + [
    (target, fault) for target in FILES for fault in FAULTS if not fault.startswith("sticky")
]


@pytest.mark.parametrize("disk", [True, False], ids=["on the disk", "on the descriptor"])
@pytest.mark.parametrize(("target", "fault"), OBJECTS)
def test_each_object_the_loader_checks_is_refused_under_each_fault(
    vite8: Path,
    copy_bundle: Copy,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    fault: str,
    disk: bool,
) -> None:
    """The mode and owner of the root, ``.vite/``, ``assets/`` and every file read are those of
    its descriptor (a test that changes only what ``fstat`` answers must be refused as one that
    changes the disk is), and a directory that others can write is refused whether sticky or
    not."""
    owners = Owners(monkeypatch, ("stat", "lstat", "fstat") if disk else ("fstat",))
    names = sorted(os.listdir(vite8 / "assets")) if target == "assets/*" else [target]
    assert len(names) >= 1
    for number, name in enumerate(names):
        root = copy_bundle(vite8, tmp_path / f"copy{number}")
        relative = f"assets/{name}" if target == "assets/*" else name
        damage(root / relative, fault, owners, disk=disk)
        label = str(root) if not relative else relative + ("/" if relative in DIRECTORIES else "")
        assert problems(root) == [
            *refused_as(fault, label, root)[0],
            f"{root}{HEADER}",
            *refused_as(fault, label, root)[1],
        ], (target, name, fault)


# --- The way to the root -------------------------------------------------------------------------


def _at(tmp_path: Path, bundle: Path) -> Path:
    """The bundle moved to ``web/dist``, so that its directory is a directory of its own."""
    web = tmp_path / "web"
    web.mkdir()
    shutil.move(bundle, web / "dist")
    return web / "dist"


@pytest.mark.parametrize("bit", [0o020, 0o002])
@pytest.mark.parametrize("sticky", [False, True])
def test_the_directory_the_root_lies_in_is_refused_when_others_can_replace_the_root(
    bundle_dir: Path, tmp_path: Path, bit: int, sticky: bool
) -> None:
    root = _at(tmp_path, bundle_dir)
    os.chmod(root.parent, 0o755 | bit | (0o1000 if sticky else 0))
    if sticky:  # as /tmp: they may add entries, but not replace another's
        assert load_bundle(root).files == 12
        return
    listed, commands = remedy_of(problems(root))
    assert listed == [
        f"{root}: its group or others can write its directory, and so replace it ({root.parent})"
    ]
    assert commands == [f"chmod go-w {root.parent}"]


@pytest.mark.parametrize("bit", [0o020, 0o002])
@pytest.mark.parametrize("fault", ["writable", "sticky, the link foreign", "sticky, the link ours"])
def test_a_link_on_the_way_in_a_directory_others_can_write_is_refused(
    bundle_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bit: int,
    fault: str,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    os.symlink(bundle_dir, shared / "web")
    owners = Owners(monkeypatch)
    os.chmod(shared, 0o755 | bit | (0o1000 if fault.startswith("sticky") else 0))
    if fault == "sticky, the link foreign":
        owners.give(shared / "web", FOREIGN)
    if fault == "sticky, the link ours":
        assert load_bundle(shared / "web").files == 12
        return
    listed, commands = remedy_of(problems(shared / "web"))
    assert listed == [
        f"{shared / 'web'}: a symbolic link on its way lies in a directory its group or "
        f"others can write, and so could be swapped ({shared})"
    ]
    assert commands == [f"chmod go-w {shared}"]


def test_a_problem_of_the_way_is_refused_with_those_of_the_modes_and_the_remedy_once(
    bundle_dir: Path, tmp_path: Path
) -> None:
    """The way is collected as the modes below it are, not refused at once: one refusal that
    lists both, and one remedy that names the directory and the tree."""
    root = _at(tmp_path, bundle_dir)
    os.chmod(root.parent, 0o775)
    os.chmod(root / "assets", 0o775)
    found = problems(root)
    listed, commands = remedy_of(found)
    assert listed == [
        f"{root}: its group or others can write its directory, and so replace it ({root.parent})",
        "assets/: writable by group or others",
    ]
    assert commands == [f"chmod go-w {root.parent}", f"chmod -R go-w {root}"]
    assert sum(line.endswith(HEADER) for line in found) == 1


def test_a_problem_of_the_way_is_listed_with_the_structural_refusal_that_stops_the_walk(
    bundle_dir: Path, tmp_path: Path
) -> None:
    root = _at(tmp_path, bundle_dir)
    os.chmod(root.parent, 0o775)
    (root / "favicon.ico").write_bytes(b"")
    listed, commands = remedy_of(problems(root))
    assert any("'favicon.ico': not a file of" in line for line in listed), listed
    assert any("its group or others can write its directory" in line for line in listed), listed
    assert commands == [f"chmod go-w {root.parent}"]


# --- The bound of what is listed -----------------------------------------------------------------


@pytest.mark.parametrize("count", [MAX_LISTED - 1, MAX_LISTED, MAX_LISTED + 1, MAX_LISTED + 7])
def test_at_most_max_listed_problems_are_listed_and_the_rest_counted(
    bundle_dir: Path, count: int
) -> None:
    for number in range(MAX_LISTED + 7):
        written = bundle_dir / "assets" / f"extra{number:02}-AbCd1234.js"
        written.write_bytes(b"")
        if number < count:
            os.chmod(written, 0o664)
    found = problems(bundle_dir)
    listed, commands = remedy_of(found)
    expected = [
        f"assets/extra{number:02}-AbCd1234.js: writable by group or others"
        for number in range(count)
    ]
    if count > MAX_LISTED:
        expected = [*expected[:MAX_LISTED], f"... and {count - MAX_LISTED} more"]
    assert listed == expected
    assert commands == [f"chmod -R go-w {bundle_dir}"]


# --- The remedy clears the refusal, in every layout ----------------------------------------------


def _modes(tree: Path, directory: int = 0o775, file: int = 0o664) -> None:
    """A checkout or an install under umask 002."""
    for here, _, files in os.walk(tree):
        os.chmod(here, directory)
        for name in files:
            os.chmod(Path(here, name), file)


def _link(bundle: Path) -> Path:
    """``dist -> dist-2026``: the current build is the link's target."""
    dated = bundle.parent / "dist-2026"
    bundle.rename(dated)
    os.symlink("dist-2026", bundle.parent / "dist")
    return bundle.parent / "dist"


def _foreign(tree: Path, owners: Owners) -> None:
    owners.give(tree, FOREIGN)
    for here, names, files in os.walk(tree):
        for name in (*names, *files):
            owners.give(Path(here, name), FOREIGN)


def _clean(bundle: Path, tmp: Path, owners: Owners) -> Path:
    return bundle


def _plain(bundle: Path, tmp: Path, owners: Owners) -> Path:
    _modes(bundle)
    return bundle


def _linked(bundle: Path, tmp: Path, owners: Owners) -> Path:
    _modes(bundle)
    return _link(bundle)


def _linked_and_foreign(bundle: Path, tmp: Path, owners: Owners) -> Path:
    root = _link(bundle)
    _foreign(bundle.parent / "dist-2026", owners)
    return root


def _umask_002(bundle: Path, tmp: Path, owners: Owners) -> Path:
    root = _at(tmp, bundle)
    _modes(root)
    os.chmod(root.parent, 0o775)
    return root


def _foreign_files(bundle: Path, tmp: Path, owners: Owners) -> Path:
    _foreign(bundle, owners)
    return bundle


def _writable_parent(bundle: Path, tmp: Path, owners: Owners) -> Path:
    root = _at(tmp, bundle)
    os.chmod(root.parent, 0o777)
    return root


def _link_in_writable_directory(bundle: Path, tmp: Path, owners: Owners) -> Path:
    shared = tmp / "shared"
    shared.mkdir()
    os.symlink(bundle, shared / "web")
    os.chmod(shared, 0o777)
    return shared / "web"


def _everything(bundle: Path, tmp: Path, owners: Owners) -> Path:
    root = _at(tmp, bundle)
    _modes(root.parent)
    root = _link(root)
    _foreign(root.parent / "dist-2026", owners)
    return root


LAYOUTS: dict[str, Callable[[Path, Path, Owners], Path]] = {
    "a plain root": _clean,
    "a plain root, group-writable": _plain,
    "a root that is a link to the current build": _linked,
    "a link to the current build, owned by another user": _linked_and_foreign,
    "a checkout under umask 002": _umask_002,
    "files of another user": _foreign_files,
    "a directory others can write": _writable_parent,
    "a link in a directory others can write": _link_in_writable_directory,
    "everything at once, behind a link": _everything,
}


def run(commands: list[str], owners: Owners, root: Path) -> None:
    """Run what the refusal says: a shell would run each line as it stands (no placeholder, no
    redirection), ``chmod`` for real, ``chown`` as ``Owners`` models it."""
    for line in commands:
        assert not any(character in line for character in "<>|;&$`\\"), line
        words = shlex.split(line)
        if words[0] == "chmod":
            subprocess.run(words, check=True)
        else:
            owners.chown(words)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_the_remedy_a_refusal_prints_clears_it_in_every_layout(
    bundle_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str
) -> None:
    owners = Owners(monkeypatch)
    root = LAYOUTS[layout](bundle_dir, tmp_path, owners)
    if layout == "a plain root":
        assert load_bundle(root).files == 12
        return
    found = problems(root)
    listed, commands = remedy_of(found)
    assert listed, found
    assert commands, found
    assert not any(command.split()[-1] == str(root) for command in commands if root.is_symlink())
    run(commands, owners, root)
    assert load_bundle(root).files == 12


# --- A hostile bundle's text in a refusal --------------------------------------------------------

HOSTILE = [
    "k\x1b]0;PWNED\x07",
    "k\nnext line",
    "k\rreturn",
    "k\x00nul",
    "k\x7fdel",
    "k\x85next",
    "k\u2028line",
    "k\u202ebidi\u2066",
    "k\u200bzero",
    "k" + "x" * 10_000,
]


def _write(root: Path, manifest: dict[str, Any]) -> None:
    (root / ".vite" / "manifest.json").write_bytes(json.dumps(manifest).encode())


def _held(root: Path) -> dict[str, Any]:
    held: dict[str, Any] = json.loads((root / ".vite" / "manifest.json").read_text())
    return held


def _wrong_member(manifest: dict[str, Any], key: str) -> None:
    manifest[key] = {"file": 5}


def _wrong_item(manifest: dict[str, Any], key: str) -> None:
    manifest[key] = {"file": "assets/index-C4-4bqdv.js", "imports": [5]}


def _names_no_file(manifest: dict[str, Any], key: str) -> None:
    manifest[key] = {"file": f"assets/{key}.js"}


def _imports_nothing_held(manifest: dict[str, Any], key: str) -> None:
    manifest["index.html"]["imports"].append(key)


def _a_stylesheet_that_is_not_one(manifest: dict[str, Any], key: str) -> None:
    manifest[key] = {"file": "assets/index-C4-4bqdv.js", "css": [f"assets/{key}"]}


def _an_asset_it_does_not_hold(manifest: dict[str, Any], key: str) -> None:
    manifest[key] = {"file": "assets/index-C4-4bqdv.js", "assets": [key]}


def _a_chunk_that_is_not_a_script(manifest: dict[str, Any], key: str) -> None:
    manifest[key] = {"file": "assets/index-C4-4bqdv.css"}
    manifest["index.html"]["imports"].append(key)


MANIFESTS: list[Callable[[dict[str, Any], str], None]] = [
    _wrong_member,
    _wrong_item,
    _names_no_file,
    _imports_nothing_held,
    _a_stylesheet_that_is_not_one,
    _an_asset_it_does_not_hold,
    _a_chunk_that_is_not_a_script,
]


@pytest.mark.parametrize("shape", MANIFESTS, ids=lambda shape: shape.__name__)
@pytest.mark.parametrize("key", HOSTILE, ids=lambda key: repr(key[:12]))
def test_a_manifest_key_or_value_reaches_a_refusal_as_text_and_never_as_a_control(
    bundle_dir: Path, key: str, shape: Callable[[dict[str, Any], str], None]
) -> None:
    manifest = _held(bundle_dir)
    shape(manifest, key)
    _write(bundle_dir, manifest)
    found = problems(bundle_dir)
    assert found
    for line in found:
        assert line.isprintable(), repr(line[:200])
    assert any(repr(key)[1:-1] in line for line in found), found


@pytest.mark.parametrize("where", ["assets", ""])
@pytest.mark.parametrize(
    "key", [key for key in HOSTILE if "\x00" not in key and len(key) < 200], ids=repr
)
def test_a_name_of_a_file_reaches_a_refusal_as_text_and_never_as_a_control(
    bundle_dir: Path, key: str, where: str
) -> None:
    name = key + (".js" if where else "")
    (bundle_dir / where / name).write_bytes(b"")
    found = problems(bundle_dir)
    assert len(found) == 1, found
    assert found[0].isprintable(), found
    assert repr(name)[1:-1] in found[0], found

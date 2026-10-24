"""The test site's ``import-study`` refuses before it touches anything (M4.2a-1b, D438): the name
held to the dataset-id grammar and the target to the site's own ``imports/<name>`` by ``realpath``,
a symlinked component refused, all of it before the server is stopped, anything is built, created
or removed. Nothing here starts a server or builds the web bundle: every case under test refuses
first, in a scratch site directory and on a port nothing listens on."""

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "testsite.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def environment(site: Path) -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(site.parent),
        "AIBI_TESTSITE_DIR": str(site),
        "AIBI_TESTSITE_PORT": str(free_port()),
    }


def import_study(site: Path, name: str, source: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), "import-study", name, str(source)],
        env=environment(site),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def sourced(site: Path, call: str) -> subprocess.CompletedProcess[str]:
    """``call`` run in a shell that sourced the script (which then defines and runs nothing)."""
    return subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}" && {call}'],
        env=environment(site),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def untouched(site: Path) -> bool:
    """The site holds none of what ``start`` makes: its directory may exist, with imports or not,
    but never the config, the data, the token or a pid file."""
    return not any(
        (site / name).exists()
        for name in ("aibi.toml", "data", "curator-token", "token-hash", "server.pid", "server.log")
    )


@pytest.fixture
def source(tmp_path: Path) -> Path:
    directory = tmp_path / "study"
    directory.mkdir()
    (directory / "meta_study.txt").write_text("x\n")
    return directory


@pytest.mark.parametrize("name", ["Bad", "a__b", "1a", "../x", "a/b", "a" * 65, "café", "a b"])
def test_a_bad_name_is_refused_before_anything_is_made(
    tmp_path: Path, source: Path, name: str
) -> None:
    site = tmp_path / "site"
    ran = import_study(site, name, source)
    assert ran.returncode != 0
    assert "a dataset name is" in ran.stderr
    assert not site.exists()


def test_a_missing_source_is_refused_before_anything_is_made(tmp_path: Path) -> None:
    site = tmp_path / "site"
    ran = import_study(site, "demo", tmp_path / "nothing")
    assert ran.returncode != 0
    assert "not a directory" in ran.stderr
    assert not site.exists()


def test_a_symlinked_imports_directory_is_refused_and_nothing_is_removed(
    tmp_path: Path, source: Path
) -> None:
    outside = tmp_path / "outside"
    (outside / "demo").mkdir(parents=True)
    (outside / "demo" / "keep").write_text("keep")
    (outside / "orchard").mkdir()
    (outside / "orchard" / "keep").write_text(
        "keep"
    )  # a fixture the test site's start would remove
    site = tmp_path / "site"
    site.mkdir()
    (site / "imports").symlink_to(outside)
    ran = import_study(site, "demo", source)
    assert ran.returncode != 0
    assert "symlink" in ran.stderr
    assert (outside / "demo" / "keep").read_text() == "keep"
    assert (outside / "orchard" / "keep").read_text() == "keep"
    assert untouched(site)


def test_a_symlinked_target_is_refused_and_nothing_is_removed(tmp_path: Path, source: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    site = tmp_path / "site"
    (site / "imports").mkdir(parents=True)
    (site / "imports" / "demo").symlink_to(outside)
    ran = import_study(site, "demo", source)
    assert ran.returncode != 0
    assert "symlink" in ran.stderr
    assert (outside / "keep").read_text() == "keep"
    assert untouched(site)


def test_the_fixtures_are_not_removed_through_a_symlinked_imports_directory(
    tmp_path: Path,
) -> None:
    """``start``'s import of the sample datasets removes its target first: not through a link."""
    outside = tmp_path / "outside"
    for name in ("orchard", "hostile_values"):
        (outside / name).mkdir(parents=True)
        (outside / name / "keep").write_text("keep")
    site = tmp_path / "site"
    site.mkdir()
    (site / "imports").symlink_to(outside)
    ran = sourced(site, "import_datasets")
    assert ran.returncode != 0
    assert "symlink" in ran.stderr
    for name in ("orchard", "hostile_values"):
        assert (outside / name / "keep").read_text() == "keep"


def test_the_site_directory_itself_may_be_reached_through_a_link(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "imports").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    ran = sourced(link, "study_target demo")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == f"{link}/imports/demo"


def test_the_target_is_the_sites_own_imports_directory_by_name(tmp_path: Path) -> None:
    site = tmp_path / "site"
    (site / "imports" / "demo").mkdir(parents=True)
    accepted = sourced(site, "study_target demo")
    assert accepted.returncode == 0, accepted.stderr
    assert accepted.stdout.strip() == f"{site}/imports/demo"
    missing = sourced(tmp_path / "fresh", "study_target demo")
    assert missing.returncode != 0
    assert "no imports directory" in missing.stderr
    early = sourced(tmp_path / "fresh", "study_target demo early")
    assert early.returncode == 0, early.stderr
    assert early.stdout.strip() == f"{tmp_path}/fresh/imports/demo"
    not_a_directory = tmp_path / "file-site"
    not_a_directory.mkdir()
    (not_a_directory / "imports").write_text("x")
    assert sourced(not_a_directory, "study_target demo early").returncode != 0
    for name in ("..", "a/../b", "Demo", "a__b"):
        assert sourced(site, f"study_target '{name}'").returncode != 0, name


# --- import-study past the checks: the source's path, and the two branches of its last step ------

STUBS = r"""
cmd_start() { mkdir -p "$SITE/imports"; }
running() { return 1; }
aibi_cli() {
  if [[ "$1" == status ]]; then printf '%s' "${LISTED:-}"; return 0; fi
  printf '%s\n' "$*" >> "$CLI_LOG"
}
"""


def stubbed(
    site: Path, cwd: Path, call: str, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """``call`` run in ``cwd`` in a shell that sourced the script, with the server's start and
    stop and the operator CLI replaced by stubs: nothing is started or built, nothing listens
    on any port, and the CLI's arguments are appended to ``CLI_LOG``."""
    return subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"\n{STUBS}\n{call}'],
        env={**environment(site), "CLI_LOG": str(site.parent / "cli.log"), **(extra or {})},
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_a_source_named_like_an_option_is_a_directory_and_not_an_option(tmp_path: Path) -> None:
    """``import-study demo -P`` with a directory ``-P`` in the working directory: the source is
    resolved with no option given, so the directory copied is that one, never the home directory."""
    work = tmp_path / "work"
    (work / "-P").mkdir(parents=True)
    (work / "-P" / "MARKER").write_text("the study")
    home = tmp_path / "home"
    home.mkdir()
    (home / "HOME_MARKER").write_text("the home directory")
    site = tmp_path / "site"
    ran = stubbed(site, work, "cmd_import_study demo -P", {"HOME": str(home)})
    assert ran.returncode == 0, ran.stderr
    target = site / "imports" / "demo"
    assert (target / "MARKER").read_text() == "the study"
    assert not (target / "HOME_MARKER").exists()
    assert ran.stdout.startswith(f"imported demo from {work}/-P ")


def test_a_relative_source_is_not_searched_for_through_cdpath(tmp_path: Path) -> None:
    """With ``CDPATH`` set to a directory that holds a directory of the same name, the source is
    the working directory's own, and no path a search of ``CDPATH`` prints is read as part of it."""
    work = tmp_path / "work"
    (work / "study").mkdir(parents=True)
    (work / "study" / "MARKER").write_text("the study")
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "study").mkdir(parents=True)
    (elsewhere / "study" / "OTHER").write_text("not the study")
    site = tmp_path / "site"
    ran = stubbed(site, work, "cmd_import_study demo study", {"CDPATH": str(elsewhere)})
    assert ran.returncode == 0, ran.stderr
    target = site / "imports" / "demo"
    assert (target / "MARKER").read_text() == "the study"
    assert not (target / "OTHER").exists()


def test_a_dataset_that_exists_is_reimported_and_a_new_one_imported(tmp_path: Path) -> None:
    """The last step of ``import-study``: ``reimport`` when the server lists the name (and not for
    a name that only begins with it), ``import`` otherwise; always for the onco pack, the
    target's path as the study."""
    work = tmp_path / "work"
    (work / "study").mkdir(parents=True)
    site = tmp_path / "site"
    target = f"{site}/imports/demo"
    log = site.parent / "cli.log"

    def last(listed: str, name: str = "demo") -> str:
        log.write_text("")
        ran = stubbed(site, work, f"cmd_import_study {name} study", {"LISTED": listed})
        assert ran.returncode == 0, ran.stderr
        (line,) = log.read_text().splitlines()
        return line

    assert last("demo  1 release\n") == f"reimport demo {target} --pack onco"
    assert last("other  1 release\ndemo  2 releases\n") == f"reimport demo {target} --pack onco"
    assert last("") == f"import demo {target} --pack onco"
    assert last("other  1 release\n") == f"import demo {target} --pack onco"
    assert last("demonstration  1 release\n") == f"import demo {target} --pack onco"


def test_a_source_named_dash_is_a_directory_and_not_the_previous_one(tmp_path: Path) -> None:
    """``import-study demo -`` with a directory ``-`` in the working directory, and ``OLDPWD`` set
    to another: the source is resolved by ``realpath``, which takes no operand for the previous
    directory, so what is copied is the directory named ``-``, and the existing import is replaced
    by it, never removed for a source that then fails to copy."""
    work = tmp_path / "work"
    (work / "-").mkdir(parents=True)
    (work / "-" / "MARKER").write_text("the study")
    previous = tmp_path / "previous"
    previous.mkdir()
    (previous / "OTHER").write_text("the previous directory")
    site = tmp_path / "site"
    (site / "imports" / "demo").mkdir(parents=True)
    (site / "imports" / "demo" / "KEEP").write_text("the earlier import")
    ran = stubbed(site, work, "cmd_import_study demo -", {"OLDPWD": str(previous)})
    assert ran.returncode == 0, ran.stderr
    target = site / "imports" / "demo"
    assert (target / "MARKER").read_text() == "the study"
    assert not (target / "OTHER").exists()
    assert ran.stdout.startswith(f"imported demo from {work}/- (the oncology pack)")


def test_the_scripts_root_is_not_searched_for_through_cdpath(tmp_path: Path) -> None:
    """Sourced by a relative path (``scripts/testsite.sh``) with ``CDPATH`` set to a directory
    that holds a ``scripts`` directory of its own, ``ROOT`` is the repository: one line, the
    script's own parent, and not the ``CDPATH`` directory ``cd`` would print and enter."""
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "scripts").mkdir(parents=True)
    root = SCRIPT.parents[1]
    ran = subprocess.run(
        ["bash", "-c", 'source scripts/testsite.sh && printf "%s" "$ROOT"'],
        env={**environment(tmp_path / "site"), "CDPATH": str(elsewhere)},
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == str(root.resolve())


def test_a_source_that_is_a_symlink_is_copied_and_named_by_its_real_path(tmp_path: Path) -> None:
    """The source is resolved physically (every symlink on the way), as the path the script says it
    imported and copies from."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "MARKER").write_text("the study")
    work = tmp_path / "work"
    work.mkdir()
    (work / "link").symlink_to(real)
    site = tmp_path / "site"
    ran = stubbed(site, work, "cmd_import_study demo link")
    assert ran.returncode == 0, ran.stderr
    assert (site / "imports" / "demo" / "MARKER").read_text() == "the study"
    assert ran.stdout.startswith(f"imported demo from {real} ")

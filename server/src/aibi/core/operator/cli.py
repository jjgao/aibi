"""``aibi``, the operator CLI (SPEC §11.2, D262, D267, D268).

It talks to the operator router over HTTP only, and imports no store, importer or server module,
so the server process stays the only writer of the store.

- **The server** is ``--server`` or ``AIBI_SERVER``, ``http://127.0.0.1:8000`` by default. The
  curator token goes over plain HTTP only to a loopback host; any other server is ``https``.
- **The token** comes from ``AIBI_TOKEN`` or, on a terminal, a prompt that does not echo; never
  from a flag, which would land in the shell's history and ``ps``, or a file.
- **The operator** is ``--operator`` or ``AIBI_OPERATOR``, with no default: a login name would
  record a name nobody chose (Q7).
- **HTTP**: no proxy from the environment (it would carry the token to a third party), no
  redirect followed, TLS verified (``--ca-bundle`` names a CA file); changes wait for their
  answer, reads time out after 120 seconds.
- **Output**: every string from the server is printed with control and bidi-formatting
  characters escaped (``escaped``, A6), a refusal as its code, path and message; ``--json`` prints
  each answer's JSON, indented, with those characters written as JSON escapes: the same JSON value
  the server sent.
- **Arguments** never hold a secret: one that holds a token's or a handle's shape standing alone
  (``SECRET_ALONE_RE``), as written or percent-decoded (``holds_secret``), is a usage error
  before any request (a longer word that holds the shape is a name, D265), and so is
  a dataset that is not an identifier, since a dataset goes into a URL. A usage error repeats no
  value argparse quotes (``_Parser.error``), and every message the CLI writes has token and
  handle shapes blanked.
- **Sessions** (D267): ``$XDG_STATE_HOME/aibi/sessions.json`` (``~/.local/state`` by default),
  mode 0600 in a 0700 directory, written atomically, keeps each (server, dataset)'s handle and the
  last draft this CLI saw (``SessionState``). A change sends that draft as ``expected``, never one
  fetched afresh, which would defeat conflict detection (§12.3). Opening, taking over and each
  change replace the entry; publishing and discarding forget it; a handle given by ``--handle -``
  (read from standard input, or from a prompt that does not echo on a terminal) or
  ``AIBI_HANDLE``, and ``--expected``, override it. A handle is never an argument. Two CLIs
  writing the file at once: the last writer wins.
- **Erasure keys** (D269) are read as a JSON array from standard input, or from a prompt that does
  not echo on a terminal, never from an argument: an erased person's key would stay in the
  shell's history and show in ``ps``.
- **Files** a command reads are UTF-8; one that is not is a usage error.
- **Exit codes**: 0 done, 1 refused, 2 usage (a missing token, operator or handle), 3 the server
  unreachable, 130 interrupted (the server may still finish the operation).
"""

import argparse
import contextlib
import getpass
import json
import os
import re
import ssl
import sys
import tempfile
from collections.abc import Callable, Generator, Mapping, Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import NoReturn, TextIO, cast

import httpx
from pydantic import BaseModel, JsonValue

from aibi.core.operator.auth import BIDI_FORMATTING, is_loopback_host, is_token, valid_name
from aibi.core.operator.client import Answer, OperatorClient, Refused, Unreachable
from aibi.core.schema.curation import CurationQueue
from aibi.core.schema.ids import is_identifier
from aibi.core.schema.jsonio import JsonError, parse_json
from aibi.core.schema.operator import (
    Datasets,
    DatasetState,
    DescriptorShown,
    DraftChanged,
    ErasedOut,
    ImportPublished,
    LoggedIssuance,
    ProposalsRejected,
    ProposersRan,
    Pruned,
    Rejected,
    SessionEnded,
    SessionOpened,
    SessionPublishedOut,
    Uploaded,
    Withdrawn,
)
from aibi.core.schema.output import DataSegment, Segment
from aibi.core.schema.refusals import Refusal, blank, holds_secret

DONE, REFUSED, USAGE, UNREACHABLE, INTERRUPTED = 0, 1, 2, 3, 130
HANDLE_SHAPE = re.compile(r"^ses_[A-Za-z0-9_-]{43}$")
STDIN = "-"
DEFAULT_SERVER = "http://127.0.0.1:8000"
STATE_FORMAT = "aibi.cli-sessions/1"

_FORMATTING = (*sorted(ord(character) for character in BIDI_FORMATTING), 0x2028, 0x2029)
"""Bidi formatting (the embeddings, overrides and isolates, and the marks) and the line and
paragraph separators, by code point."""
_FORMATTING_CLASS = "".join(re.escape(chr(point)) for point in _FORMATTING)
_UNSAFE = re.compile("[\x00-\x1f\x7f-\x9f" + _FORMATTING_CLASS + "]")
"""Control characters (C0, DEL, C1) and formatting characters, which could drive a terminal."""
_UNSAFE_IN_JSON = re.compile("[\x7f-\x9f" + _FORMATTING_CLASS + "]")
"""What ``json.dumps`` leaves unescaped of those: it escapes C0 already."""
_QUOTING = re.compile(
    r"^(?P<kept>.*?(?:unrecognized arguments|invalid choice|invalid [\w ]+ value|ambiguous "
    r"option)): .*$",
    re.DOTALL,
)
"""The argparse messages that quote what was given, which could be a secret or an erasure key."""


def escaped(text: str) -> str:
    """``text`` with control and bidi-formatting characters written as ``\\xNN`` or ``\\uNNNN``,
    so that text from data cannot drive the terminal (A6)."""

    def escape(found: re.Match[str]) -> str:
        point = ord(found[0])
        return f"\\x{point:02x}" if point < 0x100 else f"\\u{point:04x}"

    return _UNSAFE.sub(escape, text)


def json_text(value: JsonValue) -> str:
    """``value`` as indented JSON, those characters written as JSON escapes."""
    written = json.dumps(value, ensure_ascii=False, indent=2)
    return _UNSAFE_IN_JSON.sub(lambda found: f"\\u{ord(found[0]):04x}", written)


class Usage(Exception):  # noqa: N818 - how the CLI was used
    """A command that cannot run as given: a usage error (exit 2)."""


class _Exit(Exception):  # noqa: N818 - argparse's exit, raised rather than taken
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


_STREAMS: ContextVar[tuple[TextIO, TextIO]] = ContextVar("aibi_cli_streams")


class _Parser(argparse.ArgumentParser):
    """An argument parser that raises instead of exiting, and prints to the CLI's streams."""

    def _print_message(self, message: str, file: object = None) -> None:
        if message:
            out, err = _STREAMS.get()
            (err if file is sys.stderr else out).write(message)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if message:
            self._print_message(message, sys.stderr)
        raise _Exit(status)

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        quoting = _QUOTING.match(message)
        if quoting is not None:
            raise Usage(f"{blank(quoting['kept'])}, not repeated here; see --help")
        raise Usage(blank(message))


# --- Session state (D267) ----------------------------------------------------------------------


class StateError(Exception):
    """The session state file cannot be read as this CLI writes it."""


class SessionState:
    """Each (server, dataset)'s session handle and last draft, in a file only its owner reads."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def of(cls, environ: Mapping[str, str]) -> "SessionState":
        """``$XDG_STATE_HOME/aibi/sessions.json``, ``~/.local/state`` when that is unset or not
        absolute."""
        given = environ.get("XDG_STATE_HOME", "")
        base = Path(given) if given and os.path.isabs(given) else Path.home() / ".local" / "state"
        return cls(base / "aibi" / "sessions.json")

    def _read(self) -> dict[str, dict[str, dict[str, str]]]:
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise StateError(f"{self.path} cannot be read ({error.strerror})") from None
        try:
            value = parse_json(data)
        except JsonError:
            raise StateError(f"{self.path} is not JSON; remove it") from None
        if not isinstance(value, dict) or value.get("format") != STATE_FORMAT:
            raise StateError(f"{self.path} is not a session state file ({STATE_FORMAT})")
        sessions = value.get("sessions")
        if not isinstance(sessions, dict) or not all(
            isinstance(datasets, dict)
            and all(
                isinstance(entry, dict)
                and isinstance(entry.get("handle"), str)
                and isinstance(entry.get("draft"), str)
                for entry in datasets.values()
            )
            for datasets in sessions.values()
        ):
            raise StateError(f"{self.path} is damaged; remove it")
        return cast(dict[str, dict[str, dict[str, str]]], sessions)

    def _write(self, sessions: dict[str, dict[str, dict[str, str]]]) -> None:
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        written = json.dumps({"format": STATE_FORMAT, "sessions": sessions}, indent=2)
        descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".sessions-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(written + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)
            raise

    def get(self, server: str, dataset: str) -> tuple[str, str] | None:
        """The session's handle and the last draft this CLI saw, if it has them."""
        entry = self._read().get(server, {}).get(dataset)
        return None if entry is None else (entry["handle"], entry["draft"])

    def put(self, server: str, dataset: str, handle: str, draft: str) -> None:
        sessions = self._read()
        sessions.setdefault(server, {})[dataset] = {"handle": handle, "draft": draft}
        self._write(sessions)

    def forget(self, server: str, dataset: str) -> None:
        sessions = self._read()
        datasets = sessions.get(server, {})
        if dataset in datasets:
            del datasets[dataset]
            if not datasets:
                del sessions[server]
            self._write(sessions)


# --- The commands ----------------------------------------------------------------------------


def _parser() -> _Parser:
    parser = _Parser(prog="aibi", description="Operate an aibi server through its operator router.")
    parser.add_argument("--server", help=f"the server's URL (AIBI_SERVER; {DEFAULT_SERVER})")
    parser.add_argument("--operator", help="your name, as the audit trail records it")
    parser.add_argument("--ca-bundle", type=Path, help="the CA certificates to verify TLS with")
    parser.add_argument("--json", action="store_true", help="print the answers as they came")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    status = commands.add_parser("status", help="the datasets, or one, and their sessions")
    status.add_argument("dataset", nargs="?")
    queue = commands.add_parser("queue", help="a dataset's curation queue")
    queue.add_argument("dataset")
    queue.add_argument("--release", type=int)
    show = commands.add_parser("show", help="a descriptor of a release or of the draft")
    show.add_argument("dataset")
    show.add_argument("descriptor", nargs="?", default="dataset")
    show.add_argument("--release", help="a label, draft or sha256:<hex>")
    upload = commands.add_parser("upload", help="send a file to a dataset's upload area")
    upload.add_argument("dataset")
    upload.add_argument("file", type=Path)
    for name in ("import", "reimport"):
        importing = commands.add_parser(name, help=f"{name} a dataset")
        importing.add_argument("dataset")
        importing.add_argument(
            "source",
            help="an absolute path on the server, with --upload a file here, or with --connection "
            "a named connection of the server's configuration",
        )
        how = importing.add_mutually_exclusive_group()
        how.add_argument("--upload", action="store_true", help="send the file first")
        how.add_argument(
            "--connection", action="store_true", help="snapshot the named database connection"
        )
        importing.add_argument("--pack", help="the pack whose importer reads the source")
        if name == "import":
            importing.add_argument("--name", help="the dataset's name")
    withdraw = commands.add_parser("withdraw", help="withdraw a release")
    withdraw.add_argument("dataset")
    withdraw.add_argument("release", help="a label or sha256:<hex>")
    erase = commands.add_parser(
        "erase",
        help="erase a person's rows (D223); the key is a JSON array read from standard input, "
        "or asked for on a terminal",
    )
    erase.add_argument("dataset")
    erase.add_argument("table")
    erase.add_argument("--redact-only", action="store_true")
    proposers = commands.add_parser("proposers", help="run the curation proposers")
    proposers.add_argument("dataset")
    reject = commands.add_parser("reject", help="reject a proposal")
    reject.add_argument("dataset")
    reject.add_argument("proposal", type=int)
    reject_all = commands.add_parser(
        "reject-all",
        help="reject every open proposal of one proposer, or of every proposer of a kind, but "
        "those the open draft accepted and holds",
    )
    reject_all.add_argument("dataset")
    whose = reject_all.add_mutually_exclusive_group(required=True)
    whose.add_argument("--proposer", help="agent:<name>, model:<identifier> or importer:<name>@<v>")
    whose.add_argument("--kind", choices=("agent", "model", "importer"))

    issuance = commands.add_parser(
        "issuance", help="an issuance of the derivation log, with its document and parameters"
    )
    issuance.add_argument("issuance", help="iss: and a ULID")
    prune = commands.add_parser("prune", help="prune issuances from the derivation log")
    prune.add_argument(
        "--before",
        help="an RFC 3339 time with its offset; the configured period ([log]) by default",
    )

    session = commands.add_parser("session", help="curation sessions").add_subparsers(
        dest="action", required=True, parser_class=_Parser
    )
    actions: dict[str, argparse.ArgumentParser] = {}
    for name, summary in (
        ("open", "open a session on the latest release"),
        ("change", "apply a change request {edits} from a file, or - for standard input"),
        ("set", "set a field"),
        ("confirm", "assert the values of fields, or of every field"),
        ("remove", "remove a field"),
        ("put", "write a whole descriptor from a file"),
        ("remove-descriptor", "remove a descriptor"),
        ("accept", "accept proposals"),
        ("publish", "publish the draft"),
        ("discard", "discard the draft"),
        ("take-over", "take the session over with a new handle"),
    ):
        action = session.add_parser(name, help=summary)
        action.add_argument("dataset")
        action.add_argument(
            "--handle",
            choices=[STDIN],
            help="- reads the session's handle from standard input (or set AIBI_HANDLE), "
            "instead of the state file's",
        )
        action.add_argument("--expected", help="the draft expected, instead of the state file's")
        action.add_argument("--show-handle", action="store_true", help="print the handle")
        actions[name] = action
    actions["change"].add_argument("file")
    actions["set"].add_argument("descriptor")
    actions["set"].add_argument("pointer")
    actions["set"].add_argument("value", nargs="?", help="the value, as JSON")
    actions["set"].add_argument("--text", help="the value, as text")
    actions["confirm"].add_argument("descriptor")
    actions["confirm"].add_argument("pointers", nargs="*")
    actions["remove"].add_argument("descriptor")
    actions["remove"].add_argument("pointer")
    actions["put"].add_argument("file", type=Path)
    actions["remove-descriptor"].add_argument("descriptor")
    actions["accept"].add_argument("proposals", nargs="+", type=int)
    for name in ("set", "confirm", "put"):
        actions[name].add_argument("--evidence")
    return parser


class _Run:
    """One command, with its settings resolved."""

    def __init__(
        self,
        args: argparse.Namespace,
        client: OperatorClient,
        server: str,
        state: SessionState,
        stdin: TextIO,
        out: TextIO,
        environ: Mapping[str, str],
        prompt: Callable[[str], str],
    ) -> None:
        self.args = args
        self.client = client
        self.server = server
        self.state = state
        self.stdin = stdin
        self.out = out
        self.environ = environ
        self.prompt = prompt

    def say(self, *lines: str) -> None:
        for line in lines:
            self.out.write(line + "\n")

    def answer[M: BaseModel](self, answer: Answer[M], summary: Callable[[M], Sequence[str]]) -> M:
        if self.args.json:
            value = parse_json(answer.body)
            self.say(json_text(value))
        else:
            self.say(*summary(answer.value))
        return answer.value

    def run(self) -> int:
        command = cast(str, self.args.command)
        if command == "session":
            return self.session(cast(str, self.args.action))
        handlers: dict[str, Callable[[], object]] = {
            "status": self.status,
            "queue": self.queue,
            "show": self.show,
            "upload": self.upload,
            "import": lambda: self.importing(reimport=False),
            "reimport": lambda: self.importing(reimport=True),
            "withdraw": self.withdraw,
            "erase": self.erase,
            "proposers": self.proposers,
            "reject": self.reject,
            "reject-all": self.reject_all,
            "issuance": self.issuance,
            "prune": self.prune,
        }
        handlers[command]()
        return DONE

    # --- Reads ---

    def status(self) -> None:
        dataset = cast(str | None, self.args.dataset)
        if dataset is None:
            self.answer(self.client.datasets(), _datasets)
        else:
            self.answer(self.client.dataset(dataset), lambda state: _dataset(state))

    def queue(self) -> None:
        self.answer(self.client.queue(self.args.dataset, release=self.args.release), _queue)

    def show(self) -> None:
        found = self.client.descriptor(
            self.args.dataset, self.args.descriptor, release=self.args.release
        )
        self.answer(found, _shown)

    # --- Uploads and imports ---

    def upload(self) -> Uploaded:
        return self.answer(self.client.upload(self.args.dataset, self.args.file), _uploaded)

    def importing(self, *, reimport: bool) -> None:
        dataset = cast(str, self.args.dataset)
        source = cast(str, self.args.source)
        options: dict[str, str | None] = {"pack": self.args.pack}
        if not reimport:
            options["name"] = self.args.name
        given: dict[str, JsonValue]
        if self.args.upload:
            file = Path(source)
            sent = self.client.upload(dataset, file).value
            given = {"upload": sent.upload}
            options["original_name"] = file.name
        elif self.args.connection:
            given = {"connection": source}
        else:
            given = {"path": source}
        operation = self.client.reimport if reimport else self.client.import_
        self.answer(operation(dataset, given, **options), _imported)

    # --- Withdrawal, erasure and proposals ---

    def withdraw(self) -> None:
        release = cast(str, self.args.release)
        pin: int | str = int(release) if release.isascii() and release.isdigit() else release
        self.answer(self.client.withdraw(self.args.dataset, pin), _withdrawn)

    def key(self) -> list[JsonValue]:
        """The erasure key: a JSON array of scalars, from standard input or a prompt."""
        if self.stdin.isatty():
            written = self.prompt("Erasure key, a JSON array of the key's values: ")
        else:
            written = _read(self.stdin.read, "the erasure key")
        key = _json_argument(written, "the erasure key")
        if (
            not isinstance(key, list)
            or not key
            or any(isinstance(value, dict | list) for value in key)
        ):
            raise Usage('the erasure key is a JSON array of the key\'s values, such as ["s1"]')
        return key

    def erase(self) -> None:
        key = self.key()
        erased = self.client.erase(
            self.args.dataset, self.args.table, key, redact_only=self.args.redact_only
        )
        self.answer(erased, _erased)

    def proposers(self) -> None:
        self.answer(self.client.run_proposers(self.args.dataset), _proposers)

    def reject(self) -> None:
        self.answer(self.client.reject(self.args.dataset, self.args.proposal), _rejected)

    def reject_all(self) -> None:
        rejected = self.client.reject_all(
            self.args.dataset, proposer=self.args.proposer, kind=self.args.kind
        )
        self.answer(rejected, _rejected_all)

    # --- The derivation log ---

    def issuance(self) -> None:
        self.answer(self.client.issuance(self.args.issuance), _issuance)

    def prune(self) -> None:
        self.answer(self.client.prune(before=self.args.before), _pruned)

    # --- Sessions ---

    def given_handle(self) -> str | None:
        """The handle given by ``--handle -`` or ``AIBI_HANDLE``, checked for its form."""
        if self.args.handle == STDIN:
            if getattr(self.args, "file", None) == STDIN:
                raise Usage(
                    "standard input gives the handle or the change request, not both: set "
                    "AIBI_HANDLE instead"
                )
            if self.stdin.isatty():
                given = self.prompt("Session handle: ").strip()
            else:
                given = _read(self.stdin.readline, "the handle").strip()
        else:
            given = self.environ.get("AIBI_HANDLE")
            if not given:
                return None
        if HANDLE_SHAPE.fullmatch(given) is None:
            raise Usage("that is not a session handle: ses_ and 43 base64url characters")
        return given

    def held(self, dataset: str) -> tuple[str, str]:
        """The handle and the expected draft: given, or kept in the state file."""
        given = self.given_handle()
        kept = self.state.get(self.server, dataset)
        handle = given or (kept[0] if kept else None)
        expected = cast(str | None, self.args.expected) or (kept[1] if kept else None)
        if handle is None:
            raise Usage(
                f"no session of {escaped(dataset)} is kept for this server: open or take over "
                "one, or give --handle - or AIBI_HANDLE"
            )
        if expected is None:
            raise Usage("give --expected, the draft the change starts from")
        return handle, expected

    def edits(self, action: str) -> list[JsonValue]:
        args = self.args
        evidence = {"evidence": args.evidence} if getattr(args, "evidence", None) else {}
        if action == "change":
            source = self.stdin.read if args.file == STDIN else Path(args.file).read_bytes
            request = _json_argument(_read(source, "the change request"), "the change request")
            if not isinstance(request, dict) or not isinstance(request.get("edits"), list):
                raise Usage('a change request is a JSON object {"edits": [...]}')
            return cast(list[JsonValue], request["edits"])
        if action == "set":
            if (args.value is None) == (args.text is None):
                raise Usage("give the value as JSON, or --text, and not both")
            value = args.text if args.text is not None else _json_argument(args.value, "the value")
            edit: dict[str, JsonValue] = {
                "op": "set",
                "descriptor": args.descriptor,
                "pointer": args.pointer,
                "value": value,
            }
            return [{**edit, **evidence}]
        if action == "confirm":
            confirm: dict[str, JsonValue] = {"op": "confirm", "descriptor": args.descriptor}
            if args.pointers:
                confirm["pointers"] = list(args.pointers)
            return [{**confirm, **evidence}]
        if action == "remove":
            return [{"op": "remove", "descriptor": args.descriptor, "pointer": args.pointer}]
        if action == "put":
            written = _read(Path(args.file).read_bytes, "the descriptor")
            descriptor = _json_argument(written, "the descriptor")
            return [{"op": "put", "descriptor": descriptor, **evidence}]
        if action == "remove-descriptor":
            return [{"op": "remove_descriptor", "descriptor": args.descriptor}]
        return [{"op": "accept", "proposal": proposal} for proposal in args.proposals]

    def session(self, action: str) -> int:
        dataset = cast(str, self.args.dataset)
        if action in ("open", "take-over"):
            call = self.client.open if action == "open" else self.client.take_over
            opened = call(dataset).value
            self.state.put(self.server, dataset, opened.handle, opened.draft)
            if self.args.json:
                shown = opened.model_dump(
                    mode="json", exclude=set() if self.args.show_handle else {"handle"}
                )
                self.say(json_text(shown))
            else:
                self.say(*_opened(opened, show_handle=self.args.show_handle))
            return DONE
        handle, expected = self.held(dataset)
        try:
            if action == "publish":
                self.answer(self.client.publish(dataset, handle, expected), _session_published)
                self.state.forget(self.server, dataset)
            elif action == "discard":
                self.answer(self.client.discard(dataset, handle, expected), _discarded)
                self.state.forget(self.server, dataset)
            else:
                changed = self.client.change(dataset, handle, expected, self.edits(action))
                draft = self.answer(changed, _changed).draft
                self.state.put(self.server, dataset, handle, draft)
        except Refused as refused:
            if any(str(refusal.code) == "CONFLICT" for refusal in refused.refusals):
                raise _Conflict(refused, dataset) from None
            raise
        return DONE


class _Conflict(Exception):  # noqa: N818 - a refusal with a hint
    def __init__(self, refused: Refused, dataset: str) -> None:
        super().__init__(str(refused))
        self.refused = refused
        self.dataset = dataset


def _read(source: Callable[[], str | bytes], what: str) -> str:
    """What ``source`` reads, as text: a file's bytes or a stream's text, refused (a usage
    error) unless it is UTF-8."""
    try:
        found = source()
        return found.decode("utf-8") if isinstance(found, bytes) else found
    except UnicodeDecodeError:
        raise Usage(f"{what} is not UTF-8 text") from None


def _json_argument(written: str, what: str) -> JsonValue:
    try:
        return parse_json(written)
    except JsonError as error:
        raise Usage(f"{what} is not JSON: {error.message}") from None


# --- Summaries: every string from the server escaped ------------------------------------------


def _segments(segments: Sequence[Segment]) -> str:
    return "".join(
        escaped(segment.data if isinstance(segment, DataSegment) else segment.text)
        for segment in segments
    )


def _refusal(refusal: Refusal) -> list[str]:
    where = "" if refusal.path is None else f" at {escaped(refusal.path)}"
    lines = [f"refused: {escaped(str(refusal.code))}{where}: {_segments(refusal.message)}"]
    if refusal.alternatives:
        lines.append("  alternatives: " + ", ".join(_segments([a]) for a in refusal.alternatives))
    if refusal.limit is not None:
        lines.append(f"  limit: {escaped(refusal.limit.name)} (at most {refusal.limit.max})")
    return lines


def _dataset(state: DatasetState) -> list[str]:
    latest = "none" if state.latest is None else f"@{state.latest}"
    labels = ", ".join(f"@{label.label} {label.status}" for label in state.labels)
    lines = [f"{escaped(state.dataset)}: latest {latest}; releases {labels}"]
    if state.session is not None:
        session = state.session
        lines.append(
            f"  session {session.session}, opened by {escaped(session.opened_by)}: "
            f"draft {session.draft}, from {session.base}"
        )
    return lines


def _datasets(found: Datasets) -> list[str]:
    if not found.datasets:
        return ["No dataset has a release."]
    return [line for state in found.datasets for line in _dataset(state)]


def _queue(queue: CurationQueue) -> list[str]:
    lines = [f"Curation queue of {escaped(queue.dataset)} @{queue.label} ({queue.release}):"]
    lines += [
        f"  field {escaped(item.descriptor)} {escaped(item.pointer)}: {item.status} "
        f"by {escaped(item.by)}"
        for item in queue.fields
    ]
    lines += [
        f"  undeclared {escaped(item.descriptor)} {escaped(item.pointer)}"
        for item in queue.undeclared
    ]
    lines += [
        f"  proposal {item.id}: {escaped(item.descriptor)} {escaped(item.pointer)} "
        f"by {escaped(item.proposer)}"
        + (" (accepted in the draft)" if item.accepted_in_draft else "")
        + (" (stale)" if item.stale else "")
        for item in queue.proposals
    ]
    lines += [
        f"  note {item.kind} {escaped(item.subject or '')}: {_segments(item.message)}"
        for item in queue.notes
    ]
    if queue.truncated:
        lines.append(f"  … and {queue.truncated} more")
    return lines


def _shown(shown: DescriptorShown) -> list[str]:
    label = shown.label if shown.label == "draft" else f"@{shown.label}"
    return [f"{escaped(shown.dataset)} {label} ({shown.release}):", json_text(shown.descriptor)]


def _uploaded(uploaded: Uploaded) -> list[str]:
    return [f"Uploaded {uploaded.bytes} bytes for {escaped(uploaded.dataset)}: {uploaded.upload}"]


def _proposers_lines(ran: ProposersRan | None) -> list[str]:
    if ran is None:
        return []
    lines: list[str] = []
    if ran.proposals:
        lines.append("  proposals: " + ", ".join(str(number) for number in ran.proposals))
    lines += [
        f"  skipped from {escaped(item.pack)}: {escaped(item.descriptor or '(the proposer)')} "
        f"{escaped(item.pointer or '')} ({escaped(', '.join(item.codes))})"
        for item in ran.skipped
    ]
    return lines


def _imported(published: ImportPublished) -> list[str]:
    lines = [f"Published {escaped(published.dataset)} @{published.label} ({published.manifest})"]
    if published.changes:
        lines.append(f"  {len(published.changes)} changes carried or made by the re-import")
    if published.notes:
        lines.append(f"  {len(published.notes)} notes in the import report (aibi queue)")
    return lines + _proposers_lines(published.proposers)


def _withdrawn(withdrawn: Withdrawn) -> list[str]:
    labels = ", ".join(f"@{label}" for label in withdrawn.labels)
    return [f"Withdrew {labels} of {escaped(withdrawn.dataset)}"]


def _erased(erased: ErasedOut) -> list[str]:
    labels = ", ".join(f"@{label}" for label in erased.withdrawn) or "no release"
    when = "now" if erased.redacted else "once no running query holds the releases withdrawn"
    lines = [
        f"Erased from {escaped(erased.dataset)}: withdrew {labels}; {erased.terms} terms "
        f"redacted {when}"
    ]
    if erased.uploads_pending:
        lines.append("  the upload area is still to delete: erase again")
    return lines


def _proposers(ran: ProposersRan) -> list[str]:
    return ["Ran the curation proposers", *_proposers_lines(ran)]


def _rejected(rejected: Rejected) -> list[str]:
    return [f"Rejected proposal {rejected.proposal} of {escaped(rejected.dataset)}"]


def _rejected_all(rejected: ProposalsRejected) -> list[str]:
    return [
        f"Rejected {rejected.rejected} open proposals of {escaped(rejected.dataset)}; kept "
        f"{rejected.kept} that the open draft accepted and holds"
    ]


def _issuance(logged: LoggedIssuance) -> list[str]:
    source = "" if logged.values_from == logged.id else f", its values from {logged.values_from}"
    return [
        f"{logged.id} of {logged.derivation}, by {logged.tool} at {logged.at}{source}:",
        json_text(logged.model_dump(mode="json", include={"document", "params", "sql"})),
    ]


def _pruned(pruned: Pruned) -> list[str]:
    counts = "kept" if pruned.before is None else f"recorded before {pruned.before}"
    results = "kept" if pruned.results_before is None else f"before {pruned.results_before}"
    return [
        f"Pruned {pruned.pruned} issuances, of counts {counts} and of results {results}; the "
        f"log holds {pruned.log_bytes} bytes"
    ]


def _opened(opened: SessionOpened, *, show_handle: bool) -> list[str]:
    lines = [
        f"Session {opened.session} on {escaped(opened.dataset)}: draft {opened.draft}, "
        f"from {opened.base}"
    ]
    if show_handle:
        lines.append(f"  handle {opened.handle}")
    return lines


def _changed(changed: DraftChanged) -> list[str]:
    return [f"Draft of {escaped(changed.dataset)} is now {changed.draft}"]


def _session_published(published: SessionPublishedOut) -> list[str]:
    lines = [f"Published {escaped(published.dataset)} @{published.label}"]
    return lines + _proposers_lines(published.proposers)


def _discarded(ended: SessionEnded) -> list[str]:
    return [f"Discarded the session on {escaped(ended.dataset)}"]


# --- Running ---------------------------------------------------------------------------------


def _server(given: str) -> str:
    """The server's URL, refused unless the token would go over TLS or to a loopback host."""
    try:
        url = httpx.URL(given)
    except httpx.InvalidURL:
        raise Usage("the server is not a URL") from None
    if url.scheme not in ("http", "https") or not url.host:
        raise Usage("the server is an http or https URL")
    if url.scheme == "http" and not is_loopback_host(url.host):
        raise Usage(
            "the curator token goes over plain HTTP only to a loopback host; use https for "
            "any other server"
        )
    return given.rstrip("/")


def _token(environ: Mapping[str, str], stdin: TextIO, prompt: Callable[[str], str]) -> str:
    token = environ.get("AIBI_TOKEN")
    if token is None:
        if not stdin.isatty():
            raise Usage("set AIBI_TOKEN, or run on a terminal to be asked for the curator token")
        token = prompt("Curator token: ")
    if not is_token(token.strip()):
        raise Usage("that is not a curator token: aibi_ and 43 base64url characters")
    return token.strip()


@contextlib.contextmanager
def _http(server: str, ca_bundle: Path | None) -> Generator[httpx.Client]:
    verify: ssl.SSLContext | bool = True
    if ca_bundle is not None:
        try:
            verify = ssl.create_default_context(cafile=ca_bundle)
        except (OSError, ssl.SSLError):
            raise Usage("the CA bundle cannot be read") from None
    with httpx.Client(
        base_url=server, trust_env=False, follow_redirects=False, verify=verify
    ) as http:
        yield http


def main(
    argv: Sequence[str] | None = None,
    *,
    client: httpx.Client | None = None,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    prompt: Callable[[str], str] = getpass.getpass,
) -> int:
    """Run ``aibi`` with ``argv``; ``client`` replaces the HTTP client the CLI would make (its
    base URL is the server's), which tests use to talk to an application in process."""
    environ = os.environ if environ is None else environ
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    source = sys.stdin if stdin is None else stdin
    token = _STREAMS.set((out, err))
    try:
        return _main(argv, client, environ, source, out, err, prompt)
    finally:
        _STREAMS.reset(token)


def _main(
    argv: Sequence[str] | None,
    client: httpx.Client | None,
    environ: Mapping[str, str],
    stdin: TextIO,
    out: TextIO,
    err: TextIO,
    prompt: Callable[[str], str],
) -> int:
    try:
        given = list(sys.argv[1:] if argv is None else argv)
        if any(holds_secret(argument) for argument in given):
            raise Usage(
                "an argument has a token's or a handle's shape, and secrets are never arguments: "
                "give the token by AIBI_TOKEN, a handle by --handle - or AIBI_HANDLE"
            )
        args = _parser().parse_args(given)
        dataset = getattr(args, "dataset", None)
        if dataset is not None and not is_identifier(dataset):
            raise Usage(
                "a dataset is an identifier: a lower-case letter, then lower-case letters, digits "
                "and single underscores"
            )
        server = _server(args.server or environ.get("AIBI_SERVER") or DEFAULT_SERVER)
        operator = args.operator or environ.get("AIBI_OPERATOR")
        if not operator:
            raise Usage(
                "name yourself with --operator or AIBI_OPERATOR, as the audit trail records"
            )
        if not valid_name(operator):
            raise Usage(
                "an operator's name has 1 to 200 characters, without control or bidi formatting "
                "characters, and holds no token or handle"
            )
        secret = _token(environ, stdin, prompt)
        state = SessionState.of(environ)
        with contextlib.ExitStack() as stack:
            http = (
                client if client is not None else stack.enter_context(_http(server, args.ca_bundle))
            )
            operations = OperatorClient(http, token=secret, operator=operator)
            return _Run(args, operations, server, state, stdin, out, environ, prompt).run()
    except _Exit as done:
        return done.status
    except (Usage, StateError) as problem:
        err.write(f"aibi: {escaped(blank(str(problem)))}\n")
        return USAGE
    except _Conflict as conflict:
        for line in (line for refusal in conflict.refused.refusals for line in _refusal(refusal)):
            err.write(blank(line) + "\n")
        err.write(
            "hint: the session was taken over, or changed elsewhere; `aibi session take-over "
            f"{escaped(conflict.dataset)}` continues from its current draft\n"
        )
        return REFUSED
    except Refused as refused:
        for line in (line for refusal in refused.refusals for line in _refusal(refusal)):
            err.write(blank(line) + "\n")
        return REFUSED
    except Unreachable as problem:
        err.write(f"aibi: {escaped(blank(str(problem)))}\n")
        return UNREACHABLE
    except OSError as problem:
        err.write(f"aibi: {escaped(problem.strerror or type(problem).__name__)}\n")
        return USAGE
    except KeyboardInterrupt:
        err.write("aibi: interrupted; the server may still finish the operation\n")
        return INTERRUPTED


__all__ = ["DEFAULT_SERVER", "STATE_FORMAT", "SessionState", "escaped", "json_text", "main"]

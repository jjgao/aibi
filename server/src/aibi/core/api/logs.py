"""Logs without secrets (SPEC §14, D254, D261, D267).

``WithoutSecrets`` is a logging filter that leaves nothing of a token's or a handle's shape in a
record, as written or percent-decoded (``blank_path``), whatever the record's shape, and fails
closed:

- uvicorn's access lines, records of ``uvicorn.access`` with five arguments, keep them, since
  its formatter reads them: the path loses
  its query string and each segment of a secret's shape; every other string is blanked word by
  word, the status code is kept, and an argument of any other type is written as blanked text.
  Should the line so formatted still hold a secret, the client and the request line are
  replaced whole.
- Any other record, whatever its arguments, is formatted and blanked whole, its message and its
  arguments replaced by the result. A record that cannot be formatted, by whatever exception
  its message, its arguments or their ``__str__`` raise (but ``MemoryError``, which passes), is
  written as its unformatted message, blanked, when that is a plain ``str``, and otherwise as
  one fixed line (``FORMAT_FAILED``), never with the exception's text, and its arguments are
  dropped, so that every record so filtered formats and the filter itself never raises.
- A record's exception and stack text are formatted and blanked too, and its exception info
  dropped, so that a formatter writes the blanked text rather than formatting the exception
  anew: an exception's message may quote a request.

``install`` puts the filter on the loggers that write lines of requests (``uvicorn.access``,
``uvicorn.error``, ``aibi`` and every ``aibi`` logger that logs), on the root logger, and on the
MCP SDK's loggers (``MCP_LOGGERS``), since the SDK logs what fails its validation, a message's
values included, and does so on the root logger itself (D278): a filter on a logger covers the
records made on it, wherever they go. ``logging_config`` adds it to the handlers of the logging
configuration the server runs with, and gives the root logger that configuration's default
handler, so that a record of any logger, the SDK's or another library's, reaches a filtered
handler rather than Python's last resort or a handler ``logging.basicConfig`` would add.

``install_handler(stream)``, which the loader of packs calls before it imports any (D389), puts a
filtered handler writing to ``stream`` on the root logger and makes ``logging.lastResort`` a
filtered handler at ``WARNING`` writing to the same stream, so that a record a pack's module logs
at import, on a logger that propagates or on one that does not, passes the filter; and it sends
warnings to logging (``logging.captureWarnings``). It is idempotent: each handler is installed
once, and moved to ``stream`` if it wrote elsewhere.

Every handler this module installs or configures (``RootHandler``, ``LastResort`` and the
handlers of ``logging_config``) is ``Quiet``: a record it cannot write (``Handler.handleError``)
is reported by one fixed line of the core's (``NOT_WRITTEN``) on the handler's own stream, never
by logging's default report on ``sys.stderr``, which quotes the record's message, its arguments,
the traceback and the source lines of the call that made it, none of them filtered (D389).
"""

import contextlib
import logging
import logging.config
from typing import Any, TextIO, cast

from aibi.core.operator.auth import blank_path
from aibi.core.schema.refusals import SECRET_BLANK

MCP_LOGGERS = (
    "mcp",
    "mcp.server.lowlevel.server",
    "mcp.server.streamable_http",
    "mcp.server.streamable_http_manager",
    "mcp.server.transport_security",
    "mcp.shared.session",
)
"""The MCP SDK's loggers on the server's paths."""
LOGGERS = ("", "uvicorn.access", "uvicorn.error", "aibi", "aibi.core.api.protection", *MCP_LOGGERS)
"""The loggers the filter is put on: the root logger, uvicorn's, the server's own and the MCP
SDK's."""
FILTER = "without_secrets"
"""The filter's name in a logging configuration."""
ACCESS = "uvicorn.access"
"""The logger of uvicorn's access lines, the only records whose five arguments are kept."""
NOT_WRITTEN = "aibi-server: a log record could not be written\n"
"""What a ``Quiet`` handler writes for a record it could not write."""
FORMAT_FAILED = "a log record that could not be formatted"
"""What the filter writes for a record that does not format and whose message is not a plain
``str``."""

_FORMATTER = logging.Formatter()


def blank_words(text: str) -> str:
    """``text`` with each word, and each path segment in it, that holds a token's or a handle's
    shape, as written or percent-decoded, blanked."""
    return "\n".join(
        " ".join(blank_path(word) for word in line.split(" ")) for line in text.split("\n")
    )


def _text(value: object) -> str | None:
    """``str(value)``, or ``None`` if it raises: what a value's ``__str__`` raises is not
    the filter's to pass on, but a ``MemoryError`` is."""
    try:
        return str(value)
    except MemoryError:
        raise
    except Exception:  # a pack's value: whatever it raises, its text is not written
        return None


def _access_argument(index: int, argument: object) -> object:
    try:
        if index == 2 and isinstance(argument, str):
            return blank_path(argument.partition("?")[0])
        if isinstance(argument, str):
            return blank_words(argument)
        if isinstance(argument, int) and not isinstance(argument, bool):
            return argument
        text = _text(argument)
        return FORMAT_FAILED if text is None else blank_words(text)
    except MemoryError:
        raise
    except Exception:  # a str subclass that raises from its own methods
        return FORMAT_FAILED


def _clean(record: logging.LogRecord) -> bool:
    """Whether the record's message formats, holding nothing of a secret's shape."""
    try:
        written = record.getMessage()
    except MemoryError:
        raise
    except Exception:  # whatever the message, its arguments or their ``__str__`` raise
        return False
    return blank_words(written) == written


def _formatted(record: logging.LogRecord) -> str:
    """The record's message with its arguments, else its message as it is if that is a plain
    ``str``, else ``FORMAT_FAILED``: never the text of what failed."""
    try:
        return record.getMessage()
    except MemoryError:
        raise
    except Exception:  # whatever the message, its arguments or their ``__str__`` raise
        message: object = record.msg
        return message if type(message) is str else FORMAT_FAILED


class WithoutSecrets(logging.Filter):
    """The filter of the module docstring."""

    def filter(self, record: logging.LogRecord) -> bool:
        arguments = record.args
        if record.name == ACCESS and isinstance(arguments, tuple) and len(arguments) == 5:
            message = _text(record.msg)
            if message is None:
                record.msg = FORMAT_FAILED
                record.args = None
            else:
                record.msg = blank_words(message)
                blanked = tuple(
                    _access_argument(index, argument) for index, argument in enumerate(arguments)
                )
                record.args = blanked
                if not _clean(record):
                    status = blanked[4] if isinstance(blanked[4], int) else 0
                    record.args = (SECRET_BLANK, "-", SECRET_BLANK, "-", status)
        else:
            record.msg = blank_words(_formatted(record))
            record.args = None
        if record.exc_info is not None:
            record.exc_text = record.exc_text or _FORMATTER.formatException(record.exc_info)
            record.exc_info = None
        if record.exc_text:
            record.exc_text = blank_words(record.exc_text)
        if record.stack_info:
            record.stack_info = blank_words(record.stack_info)
        return True


def install() -> None:
    """Put ``WithoutSecrets`` on each of ``LOGGERS``, once: a logger's filters outlast any
    logging configuration applied later."""
    for name in LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(found, WithoutSecrets) for found in logger.filters):
            logger.addFilter(WithoutSecrets())


class Quiet(logging.Handler):
    """A handler that reports a record it could not write by ``NOT_WRITTEN`` alone, on its own
    stream if it has one, and otherwise not at all: nothing of the record, the exception or the
    stack (D389). A stream that cannot be written gets nothing, and nothing is raised."""

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - logging's name
        stream: object = getattr(self, "stream", None)
        if stream is None:
            return
        with contextlib.suppress(Exception):  # a stream that cannot be written is left alone
            cast(TextIO, stream).write(NOT_WRITTEN)
            cast(TextIO, stream).flush()


class StreamHandler(Quiet, logging.StreamHandler[TextIO]):
    """``logging.StreamHandler``, ``Quiet``: the class ``logging_config`` gives uvicorn's
    handlers."""


class RootHandler(StreamHandler):
    """The filtered handler ``install_handler`` puts on the root logger."""


class LastResort(StreamHandler):
    """The filtered handler ``install_handler`` makes ``logging.lastResort``."""


def quiet_class(given: object) -> type[logging.Handler]:
    """The ``Quiet`` class of a logging configuration's handler class, a class or its dotted
    name: ``StreamHandler`` for ``logging.StreamHandler``, the class itself if it is ``Quiet``,
    and otherwise a ``Quiet`` subclass of it."""
    found: object = given
    if not isinstance(given, type):
        found = logging.config.BaseConfigurator({}).resolve(str(given))
    if not isinstance(found, type) or not issubclass(found, logging.Handler):
        raise ValueError("a logging configuration's handler class is a logging.Handler")
    if issubclass(found, Quiet):
        return found
    if found is logging.StreamHandler:
        return StreamHandler
    return type(found.__name__, (Quiet, found), {"__module__": __name__})


def install_handler(stream: TextIO) -> None:
    """The handlers of the module docstring, on ``stream``, each once."""
    root = logging.getLogger()
    found = next((one for one in root.handlers if type(one) is RootHandler), None)
    if found is None:
        handler = RootHandler(stream)
        handler.addFilter(WithoutSecrets())
        root.addHandler(handler)
    elif found.stream is not stream:
        found.setStream(stream)
    resort = logging.lastResort
    if type(resort) is not LastResort or resort.stream is not stream:
        made = LastResort(stream)
        made.setLevel(logging.WARNING)
        made.addFilter(WithoutSecrets())
        logging.lastResort = made
    logging.captureWarnings(True)


def logging_config(base: dict[str, Any]) -> dict[str, Any]:
    """``base``, a ``logging.config.dictConfig`` configuration such as uvicorn's, with
    ``WithoutSecrets`` on every handler, each of its ``Quiet`` class (``quiet_class``), and the
    ``aibi`` loggers and the root logger writing to its default handler rather than to Python's
    last resort. A handler made by a factory (``"()"``) is refused (``ValueError``): its class is
    not known before it is made."""
    handlers: dict[str, dict[str, Any]] = {}
    for name, handler in base.get("handlers", {}).items():
        if "()" in handler or "class" not in handler:
            raise ValueError(f"the logging configuration's handler {name!r} names no class")
        handlers[name] = {
            **handler,
            "class": quiet_class(handler["class"]),
            "filters": [*handler.get("filters", []), FILTER],
        }
    loggers: dict[str, Any] = dict(base.get("loggers", {}))
    root: dict[str, Any] = dict(base.get("root", {}))
    if "default" in handlers:
        loggers["aibi"] = {"handlers": ["default"], "level": "INFO", "propagate": False}
        root = {"level": "WARNING", **root, "handlers": ["default"]}
    configured = {
        **base,
        "filters": {**base.get("filters", {}), FILTER: {"()": WithoutSecrets}},
        "handlers": handlers,
        "loggers": loggers,
    }
    if root:
        configured["root"] = root
    return configured


__all__ = [
    "ACCESS",
    "FILTER",
    "FORMAT_FAILED",
    "LOGGERS",
    "MCP_LOGGERS",
    "NOT_WRITTEN",
    "LastResort",
    "Quiet",
    "RootHandler",
    "StreamHandler",
    "WithoutSecrets",
    "blank_words",
    "install",
    "install_handler",
    "logging_config",
    "quiet_class",
]

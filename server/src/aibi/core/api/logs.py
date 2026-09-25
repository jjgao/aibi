"""Logs without secrets (SPEC §14, D254, D261, D267).

``WithoutSecrets`` is a logging filter that leaves nothing of a token's or a handle's shape in a
record, as written or percent-decoded (``blank_path``), whatever the record's shape, and fails
closed:

- uvicorn's access lines keep their five arguments, which its formatter reads: the path loses
  its query string and each segment of a secret's shape; every other string is blanked word by
  word, the status code is kept, and an argument of any other type is written as blanked text.
  Should the line so formatted still hold a secret, the client and the request line are
  replaced whole.
- Any other record is formatted and blanked whole, its message and its arguments replaced by
  the result.
- A record's exception and stack text are formatted and blanked too, and its exception info
  dropped, so that a formatter writes the blanked text rather than formatting the exception
  anew: an exception's message may quote a request.

``install`` puts the filter on the loggers that write lines of requests (``uvicorn.access``,
``uvicorn.error``, ``aibi`` and every ``aibi`` logger that logs), which covers what they log
wherever it goes; ``logging_config`` adds it to the handlers of the logging configuration the
server runs with, which covers what any logger's records propagate to them.
"""

import logging
from typing import Any

from aibi.core.operator.auth import blank_path
from aibi.core.schema.refusals import SECRET_BLANK

LOGGERS = ("uvicorn.access", "uvicorn.error", "aibi", "aibi.core.api.protection")
"""The loggers the filter is put on: uvicorn's, and the server's own."""
FILTER = "without_secrets"
"""The filter's name in a logging configuration."""

_FORMATTER = logging.Formatter()


def blank_words(text: str) -> str:
    """``text`` with each word, and each path segment in it, that holds a token's or a handle's
    shape, as written or percent-decoded, blanked."""
    return "\n".join(
        " ".join(blank_path(word) for word in line.split(" ")) for line in text.split("\n")
    )


def _access_argument(index: int, argument: object) -> object:
    if index == 2 and isinstance(argument, str):
        return blank_path(argument.partition("?")[0])
    if isinstance(argument, str):
        return blank_words(argument)
    if isinstance(argument, int) and not isinstance(argument, bool):
        return argument
    return blank_words(str(argument))


def _clean(record: logging.LogRecord) -> bool:
    """Whether the record's message formats, holding nothing of a secret's shape."""
    try:
        written = record.getMessage()
    except (TypeError, ValueError):
        return False
    return blank_words(written) == written


class WithoutSecrets(logging.Filter):
    """The filter of the module docstring."""

    def filter(self, record: logging.LogRecord) -> bool:
        arguments = record.args
        if isinstance(arguments, tuple) and len(arguments) == 5:
            record.msg = blank_words(str(record.msg))
            blanked = tuple(
                _access_argument(index, argument) for index, argument in enumerate(arguments)
            )
            record.args = blanked
            if not _clean(record):
                status = blanked[4] if isinstance(blanked[4], int) else 0
                record.args = (SECRET_BLANK, "-", SECRET_BLANK, "-", status)
        else:
            try:
                written = record.getMessage()
            except (TypeError, ValueError):
                written = str(record.msg)
            record.msg = blank_words(written)
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


def logging_config(base: dict[str, Any]) -> dict[str, Any]:
    """``base``, a ``logging.config.dictConfig`` configuration such as uvicorn's, with
    ``WithoutSecrets`` on every handler, and the ``aibi`` loggers writing to its default handler
    rather than to Python's last resort."""
    handlers: dict[str, dict[str, Any]] = {
        name: {**handler, "filters": [*handler.get("filters", []), FILTER]}
        for name, handler in base.get("handlers", {}).items()
    }
    loggers: dict[str, Any] = dict(base.get("loggers", {}))
    if "default" in handlers:
        loggers["aibi"] = {"handlers": ["default"], "level": "INFO", "propagate": False}
    return {
        **base,
        "filters": {**base.get("filters", {}), FILTER: {"()": WithoutSecrets}},
        "handlers": handlers,
        "loggers": loggers,
    }


__all__ = ["FILTER", "LOGGERS", "WithoutSecrets", "blank_words", "install", "logging_config"]

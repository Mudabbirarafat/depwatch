"""Logging setup.

The ingestion path is the part most likely to fail quietly. A registry changes
a payload shape, a retry budget runs out, a record fails validation - none of
that raises, by design, because the run records it and carries on. Without logs
the only evidence is a number in a table nobody is watching.

Output is plain text by default and JSON when LOG_FORMAT=json, because a
platform's log search is only useful when the fields are actually fields rather
than a sentence somebody has to write a regex against.
"""

import json
import logging
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime

LOGGER_NAME = "depwatch"

# Everything a LogRecord carries on its own. Anything else was passed as
# `extra=` by the caller and is therefore part of the event worth emitting.
_STANDARD_FIELDS = frozenset(
    vars(logging.LogRecord(name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None))
) | {"message", "asctime", "taskName"}

TEXT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the caller's `extra` fields promoted."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(
            {k: v for k, v in record.__dict__.items() if k not in _STANDARD_FIELDS}
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # default=str so an unexpected object in `extra` degrades to its repr
        # instead of taking the process down from inside the logger.
        return json.dumps(payload, default=str)


def configure(
    level: str | None = None,
    fmt: str | None = None,
    stream: Callable | None = None,
) -> logging.Logger:
    """Attach a handler to the `depwatch` logger. Safe to call more than once."""
    level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    fmt = (fmt or os.environ.get("LOG_FORMAT", "text")).lower()

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT))

    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()  # idempotent: a second call replaces, not duplicates
    logger.setLevel(level)
    logger.addHandler(handler)
    # uvicorn configures the root logger too, and without this every line from
    # this package would be printed twice under the server.
    logger.propagate = False
    return logger

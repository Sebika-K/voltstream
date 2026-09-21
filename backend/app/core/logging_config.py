"""Structured logging (Roadmap 5.2, Contract sections 52-53).

Plain-text logs are written for humans to read one line at a time. Structured
logs are written so a *program* (or a human with `grep`/`jq`) can pick them
apart: every line is one JSON object with named fields, e.g.

    {"timestamp": "2026-09-21T16:03:17.668Z", "level": "INFO", "service": "backend",
     "event": "request_completed", "request_id": "3f2a...", "method": "POST",
     "path": "/api/v1/telemetry/batch", "status_code": 201, "duration_ms": 17.4}

That makes a question like "show me everything that happened during the one
request that failed" a simple filter on `request_id`, instead of reading
through thousands of lines by eye.

This module only uses Python's standard library: it is a formatter plus a
setup function, not a new logging framework.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import traceback
from datetime import UTC, datetime

# The current request's ID, visible to *any* code that runs while that request
# is being handled -- without passing it through every function call. A
# `ContextVar` is Python's tool for exactly this: it holds one value per
# request even when many requests are being handled concurrently by asyncio
# (a plain global variable would get overwritten by whichever request ran last).
# The request middleware (app/core/request_context.py) sets it; the formatter
# below reads it and stamps it on every log line automatically.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

# Every attribute a plain `LogRecord` already has. Anything ELSE on a record
# must have been passed by the caller via `extra={...}`, and those are the
# custom fields (battery_id, duration_ms, ...) we copy into the output.
_STANDARD_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}

# Marks the handler this module installs, so calling `configure_logging` again
# replaces *our* handler instead of stacking a second one (which would print
# every line twice) -- and leaves any handler someone else added alone (for
# example pytest's own log-capturing handler).
_OWN_HANDLER_MARK = "_voltstream_handler"


def _extra_fields(record: logging.LogRecord) -> dict[str, object]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_RECORD_ATTRS and not key.startswith("_")
    }


class JsonFormatter(logging.Formatter):
    """Turns a log record into one line of JSON (Contract section 52's fields)."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service,
            "event": record.getMessage(),
        }

        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id

        payload.update(_extra_fields(record))

        if record.exc_info and record.exc_info[0] is not None:
            exc_type, exc_value, _ = record.exc_info
            payload["error"] = f"{exc_type.__name__}: {exc_value}"
            payload["stack"] = "".join(traceback.format_exception(*record.exc_info))

        # `default=str`: if a caller passes something JSON can't represent (a
        # datetime, a UUID), write its text form rather than crashing -- a
        # logging call must never be the thing that takes a request down.
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """A human-friendly alternative for local development: one readable line,
    with the same extra fields appended as `key=value` pairs."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        fields = _extra_fields(record)
        request_id = request_id_var.get()
        if request_id is not None:
            fields = {"request_id": request_id, **fields}
        if fields:
            line += " " + " ".join(f"{key}={value}" for key, value in fields.items())
        return line


def configure_logging(
    *, level: str = "INFO", log_format: str = "json", service: str = "backend"
) -> None:
    """Send all log output through one handler, in the chosen format.

    Safe to call more than once. An unrecognized `level` raises immediately
    (Contract section 54: invalid configuration must fail loudly rather than be
    silently replaced by a default).
    """
    root = logging.getLogger()

    for handler in list(root.handlers):
        if getattr(handler, _OWN_HANDLER_MARK, False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    setattr(handler, _OWN_HANDLER_MARK, True)
    handler.setFormatter(JsonFormatter(service) if log_format == "json" else TextFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn (the web server) installs its own plain-text handlers on its own
    # loggers. Remove them so its messages flow through our handler like
    # everything else, and silence its per-request "access" line: our request
    # middleware writes a structured `request_completed` line for every request
    # instead, so keeping both would just log each request twice.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False

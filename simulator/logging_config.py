"""Structured logging for the simulator (Roadmap 5.2, Contract section 52).

Same output shape as the backend's logs -- one JSON object per line with
`timestamp`, `level`, `service`, `event` plus whatever extra fields a log call
attaches -- so a single tool (or one `grep`) can read both services' logs.

This is deliberately its own small copy rather than an import from the backend:
each component owns its own code and dependencies (`simulator/` never imports
`backend/`), the same rule the ML component follows. Unlike the backend there is
no per-request "context variable" here: the simulator is the one that *creates*
each batch's request ID, so it simply passes it explicitly on the log call.

Standard library only.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

# Every attribute a plain LogRecord already has; anything else on a record was
# passed by the caller via `extra={...}` and is copied into the output.
_STANDARD_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}

# Marks the handler this module installs, so calling `configure_logging` twice
# replaces our handler instead of printing every line twice.
_OWN_HANDLER_MARK = "_voltstream_handler"


def _extra_fields(record: logging.LogRecord) -> dict[str, object]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_RECORD_ATTRS and not key.startswith("_")
    }


class JsonFormatter(logging.Formatter):
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
        payload.update(_extra_fields(record))
        if record.exc_info and record.exc_info[0] is not None:
            exc_type, exc_value, _ = record.exc_info
            payload["error"] = f"{exc_type.__name__}: {exc_value}"
        # default=str: a logging call must never crash the program over a value
        # JSON can't represent.
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Readable single line for local development, extras appended as key=value."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        fields = _extra_fields(record)
        if fields:
            line += " " + " ".join(f"{key}={value}" for key, value in fields.items())
        return line


def configure_logging(
    *, level: str = "INFO", log_format: str = "json", service: str = "simulator"
) -> None:
    """Send all log output through one handler in the chosen format.

    Safe to call more than once. An unrecognized `level` raises immediately
    (Contract section 54: invalid configuration fails loudly).
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

    # httpx writes its own INFO line for every request ('HTTP Request: POST ...
    # "201 Created"'). Our `batch_sent` line already says the same thing with more
    # useful fields, so keep httpx to warnings and above.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

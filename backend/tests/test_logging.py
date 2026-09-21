"""Tests for structured logging and request IDs (Roadmap 5.2, Contract sections 52-53)."""

from __future__ import annotations

import io
import json
import logging
import re

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging_config import (
    JsonFormatter,
    TextFormatter,
    configure_logging,
    request_id_var,
)


def _format_json(message: str = "something_happened", **kwargs) -> dict:
    """Build a real log record the way `logger.info(..., extra=...)` would, format it,
    and parse the resulting line back as JSON."""
    extra = kwargs.pop("extra", None)
    exc_info = kwargs.pop("exc_info", None)
    record = logging.LogRecord("test", logging.INFO, __file__, 1, message, (), exc_info)
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return json.loads(JsonFormatter(service="backend").format(record))


@pytest.fixture
def restore_logging():
    """`configure_logging` changes the process-wide root logger; put it back afterwards."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


# --- JsonFormatter -------------------------------------------------------------


def test_json_line_has_the_contract_fields():
    line = _format_json("telemetry_batch_processed")

    assert line["event"] == "telemetry_batch_processed"
    assert line["level"] == "INFO"
    assert line["service"] == "backend"
    # ISO-8601 in UTC, ending in Z (Contract section 5's time rules).
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", line["timestamp"])


def test_extra_fields_are_included():
    line = _format_json(
        "telemetry_batch_processed",
        extra={"received": 100, "inserted": 98, "duplicates": 2, "duration_ms": 17.4},
    )

    assert line["received"] == 100
    assert line["inserted"] == 98
    assert line["duplicates"] == 2
    assert line["duration_ms"] == 17.4


def test_request_id_is_added_from_context_and_absent_without_it():
    assert "request_id" not in _format_json()

    token = request_id_var.set("abc123")
    try:
        assert _format_json()["request_id"] == "abc123"
    finally:
        request_id_var.reset(token)


def test_exception_adds_error_and_stack():
    try:
        raise ValueError("bad thing")
    except ValueError:
        import sys

        line = _format_json("request_failed", exc_info=sys.exc_info())

    assert line["error"] == "ValueError: bad thing"
    assert "Traceback" in line["stack"]


def test_value_json_cannot_represent_does_not_crash_logging():
    import uuid

    event_id = uuid.uuid4()
    line = _format_json(extra={"event_id": event_id})

    assert line["event_id"] == str(event_id)


def test_text_formatter_appends_extra_fields_as_key_value_pairs():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", (), None)
    record.battery_id = "BAT-000001"

    rendered = TextFormatter().format(record)

    assert "hello" in rendered
    assert "battery_id=BAT-000001" in rendered


# --- configure_logging ---------------------------------------------------------


def test_configure_logging_twice_does_not_duplicate_output(restore_logging):
    configure_logging(level="INFO", log_format="json", service="backend")
    configure_logging(level="INFO", log_format="json", service="backend")

    ours = [
        h for h in logging.getLogger().handlers if getattr(h, "_voltstream_handler", False)
    ]
    assert len(ours) == 1


def test_configure_logging_writes_json_to_stdout(restore_logging, capsys):
    configure_logging(level="INFO", log_format="json", service="backend")

    logging.getLogger("some.module").info("hello_world", extra={"battery_id": "BAT-000001"})

    written = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert written["event"] == "hello_world"
    assert written["battery_id"] == "BAT-000001"


def test_unknown_log_level_fails_loudly(restore_logging):
    with pytest.raises(ValueError):
        configure_logging(level="LOUD", log_format="json", service="backend")


def test_log_format_setting_rejects_unknown_values():
    with pytest.raises(ValidationError):
        Settings(DATABASE_URL="postgresql+asyncpg://u:p@localhost/db", LOG_FORMAT="xml")


# --- request ID middleware -----------------------------------------------------


async def test_response_carries_a_generated_request_id(client):
    response = await client.get("/health")

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


async def test_well_formed_incoming_request_id_is_reused(client):
    response = await client.get("/health", headers={"X-Request-ID": "my-trace_id.42"})

    assert response.headers["x-request-id"] == "my-trace_id.42"


@pytest.mark.parametrize("bad_id", ["has spaces", "<script>", "x" * 65])
async def test_malformed_incoming_request_id_is_replaced(client, bad_id):
    response = await client.get("/health", headers={"X-Request-ID": bad_id})

    assert response.headers["x-request-id"] != bad_id
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


async def test_each_request_gets_its_own_id(client):
    first = await client.get("/health")
    second = await client.get("/health")

    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_request_completed_line_matches_the_response_header(client, caplog):
    caplog.set_level(logging.DEBUG, logger="app.request")

    response = await client.get("/health")

    completed = [r for r in caplog.records if r.getMessage() == "request_completed"]
    assert len(completed) == 1
    record = completed[0]
    assert record.request_id == response.headers["x-request-id"]
    assert record.method == "GET"
    assert record.path == "/health"
    assert record.status_code == 200
    assert record.duration_ms >= 0


async def test_request_id_does_not_leak_outside_the_request(client):
    await client.get("/health")

    assert request_id_var.get() is None


async def test_error_responses_are_logged_with_their_status(client, caplog):
    caplog.set_level(logging.INFO, logger="app.request")

    response = await client.get("/api/v1/batteries/DOES-NOT-EXIST")

    assert response.status_code == 404
    completed = [r for r in caplog.records if r.getMessage() == "request_completed"]
    assert completed and completed[-1].status_code == 404

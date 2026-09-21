"""Tests for the simulator's structured logging (Roadmap 5.2, Contract section 52).

No real backend or network: `httpx.MockTransport` stands in for the backend, the
same approach test_client.py uses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx
import pytest

from client import BackendClient
from config import SimulatorConfig
from logging_config import JsonFormatter, TextFormatter, configure_logging

EVENT = {
    "event_id": "6f1c2d3e-0000-4000-8000-000000000001",
    "battery_id": "BAT-000001",
    "timestamp": "2026-09-21T16:00:00+00:00",
    "state_of_charge": 50.0,
    "voltage": 48.0,
    "current": 1.0,
    "power_kw": 0.1,
    "temperature_c": 25.0,
    "health_percent": 99.0,
    "status": "IDLE",
}


def client_with_handler(handler) -> BackendClient:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://testserver"
    )
    return BackendClient("http://testserver", http_client=http_client)


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(201, json={"received": 1, "inserted": 1, "duplicates": 0})


def records(caplog, message: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == message]


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    saved_httpx = logging.getLogger("httpx").level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    logging.getLogger("httpx").setLevel(saved_httpx)


# --- formatter / setup ---------------------------------------------------------


def _format_json(message="something_happened", extra=None) -> dict:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, message, (), None)
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return json.loads(JsonFormatter(service="simulator").format(record))


def test_json_line_has_the_shared_fields_and_the_simulator_service_name():
    line = _format_json("batch_sent", {"received": 100, "duration_ms": 12.5})

    assert line["event"] == "batch_sent"
    assert line["level"] == "INFO"
    assert line["service"] == "simulator"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", line["timestamp"])
    assert line["received"] == 100
    assert line["duration_ms"] == 12.5


def test_text_formatter_appends_extra_fields():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", (), None)
    record.battery_id = "BAT-000001"

    assert "battery_id=BAT-000001" in TextFormatter().format(record)


def test_configure_logging_twice_installs_one_handler_and_quiets_httpx(restore_logging):
    configure_logging(level="INFO", log_format="json")
    configure_logging(level="INFO", log_format="json")

    ours = [h for h in logging.getLogger().handlers if getattr(h, "_voltstream_handler", False)]
    assert len(ours) == 1
    assert logging.getLogger("httpx").level == logging.WARNING


def test_unknown_log_level_fails_loudly(restore_logging):
    with pytest.raises(ValueError):
        configure_logging(level="LOUD")


def test_config_reads_log_settings_and_rejects_an_unknown_format(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOG_FORMAT", "text")
    config = SimulatorConfig.from_env()
    assert (config.log_level, config.log_format) == ("DEBUG", "text")

    monkeypatch.setenv("LOG_FORMAT", "xml")
    with pytest.raises(ValueError):
        SimulatorConfig.from_env()


# --- batch logging + request IDs -----------------------------------------------


def test_batch_sends_a_request_id_and_logs_the_same_one(caplog):
    caplog.set_level(logging.INFO)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request_id"] = request.headers["x-request-id"]
        return httpx.Response(201, json={"received": 3, "inserted": 2, "duplicates": 1})

    async def scenario():
        client = client_with_handler(handler)
        await client.send_batch([EVENT])
        await client.aclose()

    asyncio.run(scenario())

    assert re.fullmatch(r"[0-9a-f]{32}", seen["request_id"])
    (record,) = records(caplog, "batch_sent")
    assert record.request_id == seen["request_id"]
    assert (record.received, record.inserted, record.duplicates) == (3, 2, 1)
    assert record.status_code == 201
    assert record.duration_ms >= 0


def test_each_batch_gets_its_own_request_id(caplog):
    caplog.set_level(logging.INFO)

    async def scenario():
        client = client_with_handler(ok_handler)
        await client.send_batch([EVENT])
        await client.send_batch([EVENT])
        await client.aclose()

    asyncio.run(scenario())

    first, second = records(caplog, "batch_sent")
    assert first.request_id != second.request_id


def test_an_error_status_is_logged_as_batch_failed_and_still_raises(caplog):
    caplog.set_level(logging.INFO)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request_id"] = request.headers["x-request-id"]
        return httpx.Response(503, json={"error": {"code": "DOWN", "message": "db down"}})

    async def scenario():
        client = client_with_handler(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await client.send_batch([EVENT, EVENT])
        await client.aclose()

    asyncio.run(scenario())

    (record,) = records(caplog, "batch_failed")
    assert record.levelno == logging.ERROR
    assert record.request_id == seen["request_id"]
    assert record.status_code == 503
    assert record.event_count == 2
    assert "HTTPStatusError" in record.error
    assert not records(caplog, "batch_sent")


def test_an_unreachable_backend_is_logged_as_batch_failed_with_no_status(caplog):
    caplog.set_level(logging.INFO)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async def scenario():
        client = client_with_handler(handler)
        with pytest.raises(httpx.ConnectError):
            await client.send_batch([EVENT])
        await client.aclose()

    asyncio.run(scenario())

    (record,) = records(caplog, "batch_failed")
    assert record.status_code is None
    assert "ConnectError" in record.error


def test_an_empty_batch_sends_nothing_and_logs_nothing(caplog):
    caplog.set_level(logging.INFO)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201, json={"received": 0, "inserted": 0, "duplicates": 0})

    async def scenario():
        client = client_with_handler(handler)
        await client.send_batch([])
        await client.aclose()

    asyncio.run(scenario())

    assert calls == []
    assert not records(caplog, "batch_sent")

"""Tests for GET /metrics and the counters it exposes (Roadmap 7.1).

Metric objects in `app/core/metrics.py` are module-level, process-wide
singletons -- the same registry every test in this process shares, and the
same registry a real server process would use. That means these tests can't
assert an exact absolute value (another test elsewhere in the suite may have
already posted telemetry and bumped the same counters). Instead each test
reads a counter's value before an action and asserts it moved by exactly the
expected amount afterward -- true regardless of what else has run this
session, and still a real assertion that instrumentation actually fires.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete

from app.db.session import async_session_maker
from app.models.battery import Battery
from app.models.telemetry import Telemetry

TEST_BATTERY_ID = "BAT-900600"


def _metric_value(metrics_text: str, name: str, labels: dict[str, str] | None = None) -> float:
    """Parse one sample's value out of Prometheus text-exposition output.

    Missing entirely (e.g. a labeled series that has never been observed)
    counts as 0, matching how the metric behaves for anyone reading it --
    absent and zero mean the same thing to a scraper.
    """
    if labels:
        label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
        pattern = re.escape(f"{name}{{{label_str}}}") + r" ([0-9.eE+-]+)"
    else:
        pattern = re.escape(name) + r" ([0-9.eE+-]+)"
    match = re.search(pattern, metrics_text)
    return float(match.group(1)) if match else 0.0


def _valid_event_payload(**overrides) -> dict:
    payload = {
        "event_id": str(uuid.uuid4()),
        "battery_id": TEST_BATTERY_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state_of_charge": 55.0,
        "voltage": 49.5,
        "current": 12.3,
        "power_kw": 1.5,
        "temperature_c": 26.0,
        "health_percent": 99.0,
        "status": "CHARGING",
    }
    payload.update(overrides)
    return payload


async def _register() -> None:
    async with async_session_maker() as session:
        session.add(
            Battery(
                battery_id=TEST_BATTERY_ID,
                capacity_kwh=10.0,
                max_power_kw=5.0,
                nominal_voltage=48.0,
                profile_type="RESIDENTIAL",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


async def _cleanup() -> None:
    async with async_session_maker() as session:
        await session.execute(delete(Telemetry).where(Telemetry.battery_id == TEST_BATTERY_ID))
        await session.execute(delete(Battery).where(Battery.battery_id == TEST_BATTERY_ID))
        await session.commit()


async def test_metrics_endpoint_returns_prometheus_text_format(client):
    response = await client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    # Every metric this item requires (Roadmap 7.1's list) should be present,
    # even before anything has happened -- prometheus_client always exposes
    # a counter/gauge/histogram once it's been imported, at 0.
    for expected in (
        "voltstream_telemetry_received_total",
        "voltstream_telemetry_inserted_total",
        "voltstream_telemetry_duplicates_total",
        "voltstream_errors_total",
        "voltstream_ingestion_latency_seconds",
        "voltstream_database_latency_seconds",
        "voltstream_active_batteries",
        "voltstream_alerts_active",
        "voltstream_predictions_total",
    ):
        assert expected in body, f"{expected} missing from /metrics output"


async def test_single_telemetry_ingestion_updates_counters(client):
    await _register()
    try:
        before = (await client.get("/metrics")).text
        received_before = _metric_value(before, "voltstream_telemetry_received_total")
        inserted_before = _metric_value(before, "voltstream_telemetry_inserted_total")

        response = await client.post("/api/v1/telemetry", json=_valid_event_payload())
        assert response.status_code == 201

        after = (await client.get("/metrics")).text
        received_after = _metric_value(after, "voltstream_telemetry_received_total")
        inserted_after = _metric_value(after, "voltstream_telemetry_inserted_total")

        assert received_after == received_before + 1
        assert inserted_after == inserted_before + 1
    finally:
        await _cleanup()


async def test_duplicate_single_event_updates_duplicates_counter_not_inserted(client):
    await _register()
    try:
        payload = _valid_event_payload()
        first = await client.post("/api/v1/telemetry", json=payload)
        assert first.status_code == 201

        before = (await client.get("/metrics")).text
        inserted_before = _metric_value(before, "voltstream_telemetry_inserted_total")
        duplicates_before = _metric_value(before, "voltstream_telemetry_duplicates_total")

        # Same event_id again -- a safe, no-op retry (Roadmap 1.8).
        second = await client.post("/api/v1/telemetry", json=payload)
        assert second.status_code == 200

        after = (await client.get("/metrics")).text
        inserted_after = _metric_value(after, "voltstream_telemetry_inserted_total")
        duplicates_after = _metric_value(after, "voltstream_telemetry_duplicates_total")

        assert inserted_after == inserted_before
        assert duplicates_after == duplicates_before + 1
    finally:
        await _cleanup()


async def test_batch_ingestion_updates_received_and_inserted_by_batch_size(client):
    await _register()
    try:
        before = (await client.get("/metrics")).text
        received_before = _metric_value(before, "voltstream_telemetry_received_total")
        inserted_before = _metric_value(before, "voltstream_telemetry_inserted_total")

        events = [_valid_event_payload() for _ in range(5)]
        response = await client.post("/api/v1/telemetry/batch", json={"events": events})
        assert response.status_code == 201

        after = (await client.get("/metrics")).text
        received_after = _metric_value(after, "voltstream_telemetry_received_total")
        inserted_after = _metric_value(after, "voltstream_telemetry_inserted_total")

        assert received_after == received_before + 5
        assert inserted_after == inserted_before + 5
    finally:
        await _cleanup()


async def test_unknown_battery_updates_errors_counter_by_code(client):
    before = (await client.get("/metrics")).text
    errors_before = _metric_value(
        before, "voltstream_errors_total", labels={"code": "BATTERY_NOT_FOUND"}
    )

    response = await client.post(
        "/api/v1/telemetry", json=_valid_event_payload(battery_id="BAT-999999")
    )
    assert response.status_code == 404

    after = (await client.get("/metrics")).text
    errors_after = _metric_value(
        after, "voltstream_errors_total", labels={"code": "BATTERY_NOT_FOUND"}
    )

    assert errors_after == errors_before + 1

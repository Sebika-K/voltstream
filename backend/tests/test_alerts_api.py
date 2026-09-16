"""Tests for the alerts REST endpoints (Roadmap 3.4): `GET /api/v1/alerts`
and `PATCH /api/v1/alerts/{alert_id}` (Contract sections 26-27, TDD section
11).

Roadmap 3.3's `tests/test_anomaly_detection.py` already thoroughly covers
*whether the alerts table ends up correct* (dedup, escalation, auto-resolve).
This file is narrower and doesn't re-test that: it only covers whether this
new read/manual-write surface on top of that table behaves correctly --
filtering, pagination, and manual resolution's one Contract-mandated
guarantee (it must not disable detection).

Same "wipe the fleet tables clean, build a known dataset" approach as
`test_offline_detection.py` / `test_anomaly_detection.py`, for the same
reason: `GET /api/v1/alerts` with no filters returns *everything* in the
table, so leftover rows from another test file would change these counts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import delete

from app.db.session import async_session_maker
from app.models.alert import Alert
from app.models.battery import Battery
from app.models.battery_current_state import BatteryCurrentState
from app.models.telemetry import Telemetry


async def _wipe_fleet_tables() -> None:
    async with async_session_maker() as session:
        await session.execute(delete(Alert))
        await session.execute(delete(BatteryCurrentState))
        await session.execute(delete(Telemetry))
        await session.execute(delete(Battery))
        await session.commit()


async def _register(battery_id: str) -> None:
    async with async_session_maker() as session:
        session.add(
            Battery(
                battery_id=battery_id,
                capacity_kwh=10.0,
                max_power_kw=5.0,
                nominal_voltage=48.0,
                profile_type="RESIDENTIAL",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


async def _create_alert(
    *,
    battery_id: str,
    alert_type: str,
    severity: str,
    timestamp: datetime,
    resolved: bool = False,
) -> uuid.UUID:
    alert_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(
            Alert(
                alert_id=alert_id,
                battery_id=battery_id,
                timestamp=timestamp,
                alert_type=alert_type,
                severity=severity,
                message=f"{battery_id} {alert_type}",
                measured_value=None,
                threshold_value=None,
                resolved=resolved,
                resolved_at=timestamp if resolved else None,
            )
        )
        await session.commit()
    return alert_id


def _telemetry_payload(
    battery_id: str,
    *,
    timestamp: datetime,
    state_of_charge: float = 60.0,
    voltage: float = 48.0,
    temperature_c: float = 25.0,
    status: str = "DISCHARGING",
) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "battery_id": battery_id,
        "timestamp": timestamp.isoformat(),
        "state_of_charge": state_of_charge,
        "voltage": voltage,
        "current": 5.0,
        "power_kw": 1.0,
        "temperature_c": temperature_c,
        "health_percent": 100.0,
        "status": status,
    }


@pytest_asyncio.fixture(autouse=True)
async def _clean_slate():
    await _wipe_fleet_tables()
    yield
    await _wipe_fleet_tables()


# --------------------------------------------------------------------------
# GET /api/v1/alerts
# --------------------------------------------------------------------------


async def test_list_alerts_is_empty_when_none_exist(client):
    response = await client.get("/api/v1/alerts")
    assert response.status_code == 200
    body = response.json()
    assert body == {"alerts": [], "total": 0, "limit": 100, "offset": 0}


async def test_list_alerts_returns_everything_unfiltered(client):
    await _register("BAT-920001")
    await _register("BAT-920002")
    t0 = datetime.now(timezone.utc)
    await _create_alert(
        battery_id="BAT-920001", alert_type="LOW_SOC", severity="CRITICAL", timestamp=t0
    )
    await _create_alert(
        battery_id="BAT-920002",
        alert_type="HIGH_TEMPERATURE",
        severity="WARNING",
        timestamp=t0 + timedelta(seconds=1),
    )

    response = await client.get("/api/v1/alerts")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["alerts"]) == 2


async def test_list_alerts_is_newest_first(client):
    await _register("BAT-920003")
    t0 = datetime.now(timezone.utc)
    older_id = await _create_alert(
        battery_id="BAT-920003", alert_type="LOW_SOC", severity="WARNING", timestamp=t0
    )
    # A recurrence would need the first resolved, but for ordering purposes
    # two distinct alert_types on the same battery are enough to get two
    # unambiguously-ordered rows without violating the partial unique index.
    newer_id = await _create_alert(
        battery_id="BAT-920003",
        alert_type="HIGH_TEMPERATURE",
        severity="WARNING",
        timestamp=t0 + timedelta(seconds=5),
    )

    response = await client.get("/api/v1/alerts")
    body = response.json()
    returned_ids = [row["alert_id"] for row in body["alerts"]]
    assert returned_ids == [str(newer_id), str(older_id)]


async def test_filter_by_severity(client):
    await _register("BAT-920004")
    t0 = datetime.now(timezone.utc)
    await _create_alert(
        battery_id="BAT-920004", alert_type="LOW_SOC", severity="CRITICAL", timestamp=t0
    )
    await _create_alert(
        battery_id="BAT-920004",
        alert_type="HIGH_TEMPERATURE",
        severity="WARNING",
        timestamp=t0,
    )

    response = await client.get("/api/v1/alerts", params={"severity": "CRITICAL"})
    body = response.json()
    assert body["total"] == 1
    assert body["alerts"][0]["severity"] == "CRITICAL"


async def test_filter_by_battery_id(client):
    await _register("BAT-920005")
    await _register("BAT-920006")
    t0 = datetime.now(timezone.utc)
    await _create_alert(
        battery_id="BAT-920005", alert_type="LOW_SOC", severity="CRITICAL", timestamp=t0
    )
    await _create_alert(
        battery_id="BAT-920006", alert_type="LOW_SOC", severity="CRITICAL", timestamp=t0
    )

    response = await client.get("/api/v1/alerts", params={"battery_id": "BAT-920005"})
    body = response.json()
    assert body["total"] == 1
    assert body["alerts"][0]["battery_id"] == "BAT-920005"


async def test_filter_by_alert_type(client):
    await _register("BAT-920007")
    t0 = datetime.now(timezone.utc)
    await _create_alert(
        battery_id="BAT-920007", alert_type="LOW_SOC", severity="CRITICAL", timestamp=t0
    )
    await _create_alert(
        battery_id="BAT-920007",
        alert_type="HIGH_TEMPERATURE",
        severity="WARNING",
        timestamp=t0,
    )

    response = await client.get("/api/v1/alerts", params={"alert_type": "HIGH_TEMPERATURE"})
    body = response.json()
    assert body["total"] == 1
    assert body["alerts"][0]["alert_type"] == "HIGH_TEMPERATURE"


async def test_filter_by_resolved(client):
    await _register("BAT-920008")
    t0 = datetime.now(timezone.utc)
    await _create_alert(
        battery_id="BAT-920008", alert_type="LOW_SOC", severity="CRITICAL", timestamp=t0
    )
    await _create_alert(
        battery_id="BAT-920008",
        alert_type="HIGH_TEMPERATURE",
        severity="WARNING",
        timestamp=t0,
        resolved=True,
    )

    unresolved_only = await client.get("/api/v1/alerts", params={"resolved": "false"})
    assert unresolved_only.json()["total"] == 1
    assert unresolved_only.json()["alerts"][0]["alert_type"] == "LOW_SOC"

    resolved_only = await client.get("/api/v1/alerts", params={"resolved": "true"})
    assert resolved_only.json()["total"] == 1
    assert resolved_only.json()["alerts"][0]["alert_type"] == "HIGH_TEMPERATURE"


async def test_pagination_limit_and_offset(client):
    await _register("BAT-920009")
    t0 = datetime.now(timezone.utc)
    alert_types = ["LOW_SOC", "HIGH_TEMPERATURE", "RAPID_DISCHARGE", "VOLTAGE_ANOMALY"]
    for index, alert_type in enumerate(alert_types):
        await _create_alert(
            battery_id="BAT-920009",
            alert_type=alert_type,
            severity="WARNING",
            timestamp=t0 + timedelta(seconds=index),
        )

    page = await client.get("/api/v1/alerts", params={"limit": 2, "offset": 1})
    body = page.json()
    assert body["total"] == 4
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert len(body["alerts"]) == 2


async def test_invalid_alert_type_filter_is_rejected_with_422(client):
    response = await client.get("/api/v1/alerts", params={"alert_type": "NOT_A_REAL_TYPE"})
    assert response.status_code == 422


async def test_limit_over_the_maximum_is_rejected_with_422(client):
    response = await client.get("/api/v1/alerts", params={"limit": 501})
    assert response.status_code == 422


# --------------------------------------------------------------------------
# PATCH /api/v1/alerts/{alert_id}
# --------------------------------------------------------------------------


async def test_patch_resolves_an_unresolved_alert(client):
    await _register("BAT-920010")
    t0 = datetime.now(timezone.utc)
    alert_id = await _create_alert(
        battery_id="BAT-920010", alert_type="LOW_SOC", severity="CRITICAL", timestamp=t0
    )

    response = await client.patch(f"/api/v1/alerts/{alert_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["resolved"] is True
    assert body["resolved_at"] is not None


async def test_patch_unknown_alert_id_returns_404(client):
    response = await client.patch(f"/api/v1/alerts/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ALERT_NOT_FOUND"


async def test_patch_malformed_alert_id_returns_422(client):
    response = await client.patch("/api/v1/alerts/not-a-uuid")
    assert response.status_code == 422


async def test_patch_already_resolved_alert_is_idempotent(client):
    await _register("BAT-920011")
    t0 = datetime.now(timezone.utc)
    alert_id = await _create_alert(
        battery_id="BAT-920011",
        alert_type="LOW_SOC",
        severity="CRITICAL",
        timestamp=t0,
        resolved=True,
    )

    response = await client.patch(f"/api/v1/alerts/{alert_id}")
    assert response.status_code == 200
    assert response.json()["resolved"] is True


async def test_manual_resolution_does_not_disable_detection(client):
    """Contract section 27: manually resolving an alert MUST NOT disable
    anomaly detection -- if the underlying condition is still active, the
    system MAY recreate an active alert. This posts a low-SOC reading
    (creating a real LOW_SOC alert through the actual detection path, not a
    hand-inserted row), resolves it manually via the API, then posts another
    low-SOC reading and confirms detection fired again: a *new* alert_id,
    still unresolved, proving the manual resolve didn't suppress the rule.
    """
    await _register("BAT-920012")
    t0 = datetime.now(timezone.utc)

    await client.post(
        "/api/v1/telemetry", json=_telemetry_payload("BAT-920012", timestamp=t0, state_of_charge=5.0)
    )
    list_response = await client.get(
        "/api/v1/alerts", params={"battery_id": "BAT-920012", "alert_type": "LOW_SOC"}
    )
    first_alert_id = list_response.json()["alerts"][0]["alert_id"]

    patch_response = await client.patch(f"/api/v1/alerts/{first_alert_id}")
    assert patch_response.status_code == 200
    assert patch_response.json()["resolved"] is True

    # The condition is still active (SOC is still 5%) -- the next reading
    # must recreate an unresolved alert rather than leaving it resolved.
    await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload(
            "BAT-920012", timestamp=t0 + timedelta(seconds=5), state_of_charge=5.0
        ),
    )
    list_response_after = await client.get(
        "/api/v1/alerts",
        params={"battery_id": "BAT-920012", "alert_type": "LOW_SOC", "resolved": "false"},
    )
    body = list_response_after.json()
    assert body["total"] == 1
    assert body["alerts"][0]["alert_id"] != first_alert_id

"""Tests for rule-based anomaly detection (Roadmap 3.3, Contract sections
24-27, global invariant #13).

Every rule funnels through the same two shared helpers in
`app/services/anomaly_detection_service.py` -- `create_or_retain_alert` and
`resolve_alert_if_active` -- so dedup, severity escalation, auto-resolution,
and recurrence-after-resolution are each tested thoroughly once (against
`LOW_SOC`) rather than four times over. The remaining telemetry-triggered
rules (`HIGH_TEMPERATURE`, `VOLTAGE_ANOMALY`, `RAPID_DISCHARGE`) get a
narrower set of tests: proving each rule's own threshold math produces the
right severity and resolves correctly, since the create/dedup/resolve
mechanics underneath are already covered.

Like `test_offline_detection.py`, this wipes the fleet tables (now
including `alerts`) clean per test rather than relying on unique battery_ids
alone -- `RAPID_DISCHARGE` in particular reads `telemetry` history for a
battery, so leftover rows from another test would change its answer.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import delete, select

from app.db.session import async_session_maker
from app.models.alert import Alert
from app.models.battery import Battery
from app.models.battery_current_state import BatteryCurrentState
from app.models.telemetry import Telemetry
from app.services.anomaly_detection_service import (
    ALERT_TYPE_DEVICE_OFFLINE,
    SEVERITY_WARNING,
    create_or_retain_alert,
)
from app.services.fleet_service import get_fleet_summary
from app.services.offline_detection_service import mark_stale_batteries_offline


async def _wipe_fleet_tables() -> None:
    async with async_session_maker() as session:
        await session.execute(delete(Alert))
        await session.execute(delete(BatteryCurrentState))
        await session.execute(delete(Telemetry))
        await session.execute(delete(Battery))
        await session.commit()


async def _register(battery_id: str, *, nominal_voltage: float = 48.0) -> None:
    async with async_session_maker() as session:
        session.add(
            Battery(
                battery_id=battery_id,
                capacity_kwh=10.0,
                max_power_kw=5.0,
                nominal_voltage=nominal_voltage,
                profile_type="RESIDENTIAL",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


async def _get_unresolved_alert(battery_id: str, alert_type: str) -> Alert | None:
    async with async_session_maker() as session:
        return await session.scalar(
            select(Alert)
            .where(Alert.battery_id == battery_id)
            .where(Alert.alert_type == alert_type)
            .where(Alert.resolved.is_(False))
        )


async def _get_all_alerts(battery_id: str, alert_type: str) -> list[Alert]:
    async with async_session_maker() as session:
        result = await session.execute(
            select(Alert)
            .where(Alert.battery_id == battery_id)
            .where(Alert.alert_type == alert_type)
            .order_by(Alert.timestamp)
        )
        return list(result.scalars().all())


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
# LOW_SOC -- dedup, escalation, auto-resolve, and recurrence are all proven
# here; every other rule reuses the exact same shared helpers.
# --------------------------------------------------------------------------


async def test_low_soc_creates_warning_alert(client):
    await _register("BAT-910001")
    t0 = datetime.now(timezone.utc)

    response = await client.post(
        "/api/v1/telemetry", json=_telemetry_payload("BAT-910001", timestamp=t0, state_of_charge=15.0)
    )
    assert response.status_code in (200, 201)

    alert = await _get_unresolved_alert("BAT-910001", "LOW_SOC")
    assert alert is not None
    assert alert.severity == "WARNING"
    assert alert.measured_value == 15.0


async def test_low_soc_creates_critical_alert(client):
    await _register("BAT-910002")
    t0 = datetime.now(timezone.utc)

    response = await client.post(
        "/api/v1/telemetry", json=_telemetry_payload("BAT-910002", timestamp=t0, state_of_charge=5.0)
    )
    assert response.status_code in (200, 201)

    alert = await _get_unresolved_alert("BAT-910002", "LOW_SOC")
    assert alert is not None
    assert alert.severity == "CRITICAL"


async def test_low_soc_dedup_keeps_same_alert_id(client):
    """Contract section 26 / global invariant #13: a continuously active
    condition MUST NOT create one alert per telemetry event."""
    await _register("BAT-910003")
    t0 = datetime.now(timezone.utc)

    await client.post(
        "/api/v1/telemetry", json=_telemetry_payload("BAT-910003", timestamp=t0, state_of_charge=15.0)
    )
    first_alert = await _get_unresolved_alert("BAT-910003", "LOW_SOC")
    assert first_alert is not None

    await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload(
            "BAT-910003", timestamp=t0 + timedelta(seconds=5), state_of_charge=16.0
        ),
    )
    second_alert = await _get_unresolved_alert("BAT-910003", "LOW_SOC")
    assert second_alert is not None
    assert second_alert.alert_id == first_alert.alert_id

    all_rows = await _get_all_alerts("BAT-910003", "LOW_SOC")
    assert len(all_rows) == 1


async def test_low_soc_severity_escalates_in_place(client):
    await _register("BAT-910004")
    t0 = datetime.now(timezone.utc)

    await client.post(
        "/api/v1/telemetry", json=_telemetry_payload("BAT-910004", timestamp=t0, state_of_charge=15.0)
    )
    warning_alert = await _get_unresolved_alert("BAT-910004", "LOW_SOC")
    assert warning_alert is not None
    assert warning_alert.severity == "WARNING"

    await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload(
            "BAT-910004", timestamp=t0 + timedelta(seconds=5), state_of_charge=3.0
        ),
    )
    critical_alert = await _get_unresolved_alert("BAT-910004", "LOW_SOC")
    assert critical_alert is not None
    assert critical_alert.alert_id == warning_alert.alert_id
    assert critical_alert.severity == "CRITICAL"
    assert critical_alert.measured_value == 3.0


async def test_low_soc_auto_resolves_when_condition_clears(client):
    await _register("BAT-910005")
    t0 = datetime.now(timezone.utc)

    await client.post(
        "/api/v1/telemetry", json=_telemetry_payload("BAT-910005", timestamp=t0, state_of_charge=15.0)
    )
    alert = await _get_unresolved_alert("BAT-910005", "LOW_SOC")
    assert alert is not None

    resolve_time = t0 + timedelta(seconds=5)
    await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload("BAT-910005", timestamp=resolve_time, state_of_charge=80.0),
    )

    assert await _get_unresolved_alert("BAT-910005", "LOW_SOC") is None
    all_rows = await _get_all_alerts("BAT-910005", "LOW_SOC")
    assert len(all_rows) == 1
    assert all_rows[0].resolved is True
    assert all_rows[0].resolved_at == resolve_time


async def test_low_soc_recurrence_creates_new_alert_id(client):
    """Contract section 26: "alert history MUST therefore preserve distinct
    incidents" -- a recurrence after resolution is a new alert_id, not the
    resolved one reopened."""
    await _register("BAT-910006")
    t0 = datetime.now(timezone.utc)

    await client.post(
        "/api/v1/telemetry", json=_telemetry_payload("BAT-910006", timestamp=t0, state_of_charge=15.0)
    )
    first_alert = await _get_unresolved_alert("BAT-910006", "LOW_SOC")
    assert first_alert is not None

    await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload(
            "BAT-910006", timestamp=t0 + timedelta(seconds=5), state_of_charge=80.0
        ),
    )
    assert await _get_unresolved_alert("BAT-910006", "LOW_SOC") is None

    await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload(
            "BAT-910006", timestamp=t0 + timedelta(seconds=10), state_of_charge=10.0
        ),
    )
    second_alert = await _get_unresolved_alert("BAT-910006", "LOW_SOC")
    assert second_alert is not None
    assert second_alert.alert_id != first_alert.alert_id

    all_rows = await _get_all_alerts("BAT-910006", "LOW_SOC")
    assert len(all_rows) == 2


# --------------------------------------------------------------------------
# HIGH_TEMPERATURE
# --------------------------------------------------------------------------


async def test_high_temperature_warning_alert(client):
    await _register("BAT-910010")
    t0 = datetime.now(timezone.utc)

    await client.post(
        "/api/v1/telemetry", json=_telemetry_payload("BAT-910010", timestamp=t0, temperature_c=52.0)
    )

    alert = await _get_unresolved_alert("BAT-910010", "HIGH_TEMPERATURE")
    assert alert is not None
    assert alert.severity == "WARNING"


async def test_high_temperature_critical_alert(client):
    await _register("BAT-910011")
    t0 = datetime.now(timezone.utc)

    await client.post(
        "/api/v1/telemetry", json=_telemetry_payload("BAT-910011", timestamp=t0, temperature_c=60.0)
    )

    alert = await _get_unresolved_alert("BAT-910011", "HIGH_TEMPERATURE")
    assert alert is not None
    assert alert.severity == "CRITICAL"


async def test_high_temperature_resolves_when_condition_clears(client):
    await _register("BAT-910012")
    t0 = datetime.now(timezone.utc)

    await client.post(
        "/api/v1/telemetry", json=_telemetry_payload("BAT-910012", timestamp=t0, temperature_c=52.0)
    )
    assert await _get_unresolved_alert("BAT-910012", "HIGH_TEMPERATURE") is not None

    await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload(
            "BAT-910012", timestamp=t0 + timedelta(seconds=5), temperature_c=25.0
        ),
    )
    assert await _get_unresolved_alert("BAT-910012", "HIGH_TEMPERATURE") is None


# --------------------------------------------------------------------------
# VOLTAGE_ANOMALY -- expected_voltage = nominal_voltage * (0.9 + 0.2 * soc/100)
# ------------------------------------------------------------------------
# nominal_voltage=48.0, state_of_charge=50.0 -> expected_voltage = 48.0.
# --------------------------------------------------------------------------


async def test_voltage_anomaly_warning_alert(client):
    await _register("BAT-910020", nominal_voltage=48.0)
    t0 = datetime.now(timezone.utc)

    # 51.84V is 8% above the 48.0V expected voltage -- above the 7.5% WARNING
    # threshold, below the 12.5% CRITICAL one.
    await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload("BAT-910020", timestamp=t0, state_of_charge=50.0, voltage=51.84),
    )

    alert = await _get_unresolved_alert("BAT-910020", "VOLTAGE_ANOMALY")
    assert alert is not None
    assert alert.severity == "WARNING"


async def test_voltage_anomaly_critical_alert(client):
    await _register("BAT-910021", nominal_voltage=48.0)
    t0 = datetime.now(timezone.utc)

    # 55.2V is 15% above the 48.0V expected voltage -- above the 12.5%
    # CRITICAL threshold.
    await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload("BAT-910021", timestamp=t0, state_of_charge=50.0, voltage=55.2),
    )

    alert = await _get_unresolved_alert("BAT-910021", "VOLTAGE_ANOMALY")
    assert alert is not None
    assert alert.severity == "CRITICAL"


async def test_voltage_anomaly_resolves_when_condition_clears(client):
    await _register("BAT-910022", nominal_voltage=48.0)
    t0 = datetime.now(timezone.utc)

    await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload("BAT-910022", timestamp=t0, state_of_charge=50.0, voltage=55.2),
    )
    assert await _get_unresolved_alert("BAT-910022", "VOLTAGE_ANOMALY") is not None

    await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload(
            "BAT-910022", timestamp=t0 + timedelta(seconds=5), state_of_charge=50.0, voltage=48.0
        ),
    )
    assert await _get_unresolved_alert("BAT-910022", "VOLTAGE_ANOMALY") is None


# --------------------------------------------------------------------------
# RAPID_DISCHARGE -- the only rule that reads telemetry history rather than
# just the current event.
# --------------------------------------------------------------------------


async def test_rapid_discharge_skips_when_no_historical_reading(client):
    """The very first telemetry event a battery ever reports has nothing
    older than the rolling window to compare against -- Contract section 25
    treats this as "insufficient data," not "no alert": the rule must not
    create an alert, but it also must not error."""
    await _register("BAT-910030")
    t0 = datetime.now(timezone.utc)

    response = await client.post(
        "/api/v1/telemetry", json=_telemetry_payload("BAT-910030", timestamp=t0, state_of_charge=10.0)
    )
    assert response.status_code in (200, 201)

    assert await _get_unresolved_alert("BAT-910030", "RAPID_DISCHARGE") is None


async def test_rapid_discharge_creates_alert_with_sufficient_decline(client):
    await _register("BAT-910031")
    t0 = datetime.now(timezone.utc)

    await client.post(
        "/api/v1/telemetry", json=_telemetry_payload("BAT-910031", timestamp=t0, state_of_charge=80.0)
    )

    # 6 minutes later (past the 5-minute window), SOC has dropped 12
    # percentage points -- at or above the 10pp CRITICAL threshold.
    await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload(
            "BAT-910031", timestamp=t0 + timedelta(minutes=6), state_of_charge=68.0
        ),
    )

    alert = await _get_unresolved_alert("BAT-910031", "RAPID_DISCHARGE")
    assert alert is not None
    assert alert.severity == "CRITICAL"
    assert alert.measured_value == 12.0


async def test_rapid_discharge_no_alert_when_decline_below_threshold(client):
    await _register("BAT-910032")
    t0 = datetime.now(timezone.utc)

    await client.post(
        "/api/v1/telemetry", json=_telemetry_payload("BAT-910032", timestamp=t0, state_of_charge=80.0)
    )

    # Only a 2-point decline over the window -- well under the 5pp WARNING
    # threshold.
    await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload(
            "BAT-910032", timestamp=t0 + timedelta(minutes=6), state_of_charge=78.0
        ),
    )

    assert await _get_unresolved_alert("BAT-910032", "RAPID_DISCHARGE") is None


# --------------------------------------------------------------------------
# DEVICE_OFFLINE -- created by the offline-detection loop
# (app/services/offline_detector.py), resolved by telemetry ingestion.
# This exercises that same interaction end-to-end without needing to run
# the actual background loop.
# --------------------------------------------------------------------------


async def test_device_offline_created_then_resolved_end_to_end(client):
    await _register("BAT-910040")
    async with async_session_maker() as session:
        session.add(
            BatteryCurrentState(
                battery_id="BAT-910040",
                last_seen=datetime.now(timezone.utc) - timedelta(hours=1),
                state_of_charge=50.0,
                temperature_c=25.0,
                power_kw=1.0,
                health_percent=100.0,
                status="DISCHARGING",
            )
        )
        await session.commit()

    # Mimic one tick of app/services/offline_detector.py's loop body.
    async with async_session_maker() as session:
        newly_offline_ids = await mark_stale_batteries_offline(session)
        assert newly_offline_ids == ["BAT-910040"]
        now = datetime.now(timezone.utc)
        for battery_id in newly_offline_ids:
            await create_or_retain_alert(
                session,
                battery_id=battery_id,
                alert_type=ALERT_TYPE_DEVICE_OFFLINE,
                severity=SEVERITY_WARNING,
                message=f"{battery_id} has not reported telemetry",
                measured_value=None,
                threshold_value=10.0,
                timestamp=now,
            )
        await session.commit()

    offline_alert = await _get_unresolved_alert("BAT-910040", "DEVICE_OFFLINE")
    assert offline_alert is not None
    assert offline_alert.severity == "WARNING"

    # New telemetry arrives -- Contract section 23's recovery trigger --
    # which must resolve the DEVICE_OFFLINE alert as a side effect.
    response = await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload("BAT-910040", timestamp=datetime.now(timezone.utc)),
    )
    assert response.status_code in (200, 201)

    assert await _get_unresolved_alert("BAT-910040", "DEVICE_OFFLINE") is None


# --------------------------------------------------------------------------
# GET /api/v1/fleet/summary's active_alerts/critical_alerts now read this
# table (Roadmap 3.3 change to app/services/fleet_service.py) instead of
# always returning 0.
# --------------------------------------------------------------------------



# --------------------------------------------------------------------------
# Concurrency (Roadmap 6.4 load-test finding): create_or_retain_alert MUST
# be a single atomic UPSERT, not a SELECT-then-decide. Under load, many
# requests for the same battery_id + alert_type can race here -- a burst of
# LOW_SOC-triggering events for one battery within a batch, or this rule
# path racing the offline-detector's DEVICE_OFFLINE path, both funnel
# through this one helper. With the old check-then-insert, two concurrent
# callers could both see "no unresolved alert yet" and both try to INSERT,
# and the second would violate the partial unique index
# (`ux_alerts_battery_id_alert_type_unresolved`) instead of retaining.
# --------------------------------------------------------------------------


async def test_concurrent_low_soc_events_for_one_battery_retain_a_single_alert(client):
    await _register("BAT-910060")
    t0 = datetime.now(timezone.utc)

    # Many concurrent requests, all tripping LOW_SOC for the SAME battery at
    # once -- the exact shape of the race: every one of them will see no
    # unresolved LOW_SOC alert yet if the create/retain check is not atomic.
    responses = await asyncio.gather(
        *(
            client.post(
                "/api/v1/telemetry",
                json=_telemetry_payload("BAT-910060", timestamp=t0, state_of_charge=15.0),
            )
            for _ in range(20)
        )
    )

    assert all(r.status_code in (200, 201) for r in responses), [r.status_code for r in responses]

    alerts = await _get_all_alerts("BAT-910060", "LOW_SOC")
    assert len(alerts) == 1
    assert alerts[0].resolved is False

async def test_fleet_summary_reflects_active_and_critical_alerts(client):
    await _register("BAT-910050")
    await _register("BAT-910051")
    t0 = datetime.now(timezone.utc)

    # One WARNING (LOW_SOC) and one CRITICAL (HIGH_TEMPERATURE) alert, on
    # two different batteries. voltage=44.64 on the first is the exact
    # expected_voltage for nominal_voltage=48.0 at state_of_charge=15.0 --
    # keeping it there deliberately, so this test isn't also, coincidentally,
    # tripping VOLTAGE_ANOMALY (a third rule this test isn't about).
    await client.post(
        "/api/v1/telemetry",
        json=_telemetry_payload(
            "BAT-910050", timestamp=t0, state_of_charge=15.0, voltage=44.64
        ),
    )
    await client.post(
        "/api/v1/telemetry", json=_telemetry_payload("BAT-910051", timestamp=t0, temperature_c=60.0)
    )

    async with async_session_maker() as session:
        summary = await get_fleet_summary(session)

    assert summary.active_alerts == 2
    assert summary.critical_alerts == 1

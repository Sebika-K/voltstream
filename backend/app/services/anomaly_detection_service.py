"""Rule-based anomaly detection (Roadmap 3.3, Contract sections 24-26,
global invariant #13: "a continuously active anomaly MUST NOT create an
alert for every telemetry event").

Five rule types exist (Contract section 24): `LOW_SOC`, `HIGH_TEMPERATURE`,
`RAPID_DISCHARGE`, `VOLTAGE_ANOMALY`, `DEVICE_OFFLINE`. The first four are
evaluated here, synchronously, right after a telemetry event's current-state
UPSERT (Contract section 15 step 7: "evaluate relevant synchronous rules") --
see `evaluate_rules_for_events`, called from
`app/services/telemetry_service.py`. `DEVICE_OFFLINE` is different: nothing
about "a battery stopped sending telemetry" can be *detected* from a
telemetry event, since there isn't one -- that one is raised from
`app/services/offline_detector.py` (Roadmap 3.2) instead, using the same
`create_or_retain_alert` helper below so both paths share one
create/dedup/resolve implementation rather than two.

Every rule follows the same shape: compute a severity for the condition (or
decide it isn't active), then call exactly one of the two shared helpers --
`create_or_retain_alert` if the condition holds, `resolve_alert_if_active`
if it doesn't. Neither helper cares which rule called it; the "one
unresolved alert per battery_id + alert_type" bookkeeping (Contract section
26) lives entirely in these two functions, backed by a partial unique
database index (`app/models/alert.py`) as a hard guarantee, not just an
application-level convention.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.alert import Alert
from app.models.battery import Battery
from app.models.telemetry import Telemetry
from app.schemas.telemetry import TelemetryEvent

SEVERITY_WARNING = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"

ALERT_TYPE_LOW_SOC = "LOW_SOC"
ALERT_TYPE_HIGH_TEMPERATURE = "HIGH_TEMPERATURE"
ALERT_TYPE_RAPID_DISCHARGE = "RAPID_DISCHARGE"
ALERT_TYPE_VOLTAGE_ANOMALY = "VOLTAGE_ANOMALY"
ALERT_TYPE_DEVICE_OFFLINE = "DEVICE_OFFLINE"


# --------------------------------------------------------------------------
# Shared create/retain/resolve helpers -- every rule below calls one of
# these two functions and never touches the `alerts` table directly.
# --------------------------------------------------------------------------


async def _get_unresolved_alert(
    session: AsyncSession, battery_id: str, alert_type: str
) -> Alert | None:
    query = (
        select(Alert)
        .where(Alert.battery_id == battery_id)
        .where(Alert.alert_type == alert_type)
        .where(Alert.resolved.is_(False))
    )
    return await session.scalar(query)


async def create_or_retain_alert(
    session: AsyncSession,
    *,
    battery_id: str,
    alert_type: str,
    severity: str,
    message: str,
    measured_value: float | None,
    threshold_value: float | None,
    timestamp: datetime.datetime,
) -> None:
    """Create a new alert, or update the existing unresolved one in place.

    Contract section 26: "if a condition remains active: update/retain
    existing alert; MUST NOT create one alert per telemetry event." An
    already-unresolved alert of this exact `battery_id` + `alert_type` is
    the *same ongoing incident* -- its `severity`/`message`/`measured_value`
    are updated to reflect the latest reading (so an incident that escalates
    from WARNING to CRITICAL is visible), but its `alert_id` and original
    `timestamp` (when the incident started) are deliberately left untouched.
    Only when there is no unresolved alert yet does a new row, with a new
    `alert_id`, get created -- which is also what makes a *recurrence* after
    a previous resolution show up as a distinct incident (Contract section
    26: "alert history MUST therefore preserve distinct incidents").
    """
    existing = await _get_unresolved_alert(session, battery_id, alert_type)
    if existing is not None:
        existing.severity = severity
        existing.message = message
        existing.measured_value = measured_value
        existing.threshold_value = threshold_value
        return

    session.add(
        Alert(
            alert_id=uuid.uuid4(),
            battery_id=battery_id,
            timestamp=timestamp,
            alert_type=alert_type,
            severity=severity,
            message=message,
            measured_value=measured_value,
            threshold_value=threshold_value,
            resolved=False,
            resolved_at=None,
        )
    )


async def resolve_alert_if_active(
    session: AsyncSession,
    *,
    battery_id: str,
    alert_type: str,
    resolved_at: datetime.datetime,
) -> None:
    """Resolve the unresolved alert of this type for this battery, if any.

    Contract section 26: "when the condition clears: automatically mark
    alert resolved, set resolved_at." A no-op, not an error, when there was
    nothing unresolved to begin with -- this is called on *every* rule
    evaluation where the condition is no longer active, most of which never
    had an open alert in the first place.
    """
    existing = await _get_unresolved_alert(session, battery_id, alert_type)
    if existing is None:
        return
    existing.resolved = True
    existing.resolved_at = resolved_at


# --------------------------------------------------------------------------
# Individual rules (Contract sections 24-25)
# --------------------------------------------------------------------------


async def _evaluate_low_soc(
    session: AsyncSession, battery_id: str, state_of_charge: float, timestamp: datetime.datetime
) -> None:
    settings = get_settings()
    if state_of_charge <= settings.LOW_SOC_CRITICAL_PERCENT:
        severity, threshold = SEVERITY_CRITICAL, settings.LOW_SOC_CRITICAL_PERCENT
    elif state_of_charge <= settings.LOW_SOC_WARNING_PERCENT:
        severity, threshold = SEVERITY_WARNING, settings.LOW_SOC_WARNING_PERCENT
    else:
        await resolve_alert_if_active(
            session, battery_id=battery_id, alert_type=ALERT_TYPE_LOW_SOC, resolved_at=timestamp
        )
        return

    await create_or_retain_alert(
        session,
        battery_id=battery_id,
        alert_type=ALERT_TYPE_LOW_SOC,
        severity=severity,
        message=f"{battery_id} state of charge is {state_of_charge:.1f}%, at or below the "
        f"{severity.lower()} threshold of {threshold:.1f}%",
        measured_value=state_of_charge,
        threshold_value=threshold,
        timestamp=timestamp,
    )


async def _evaluate_high_temperature(
    session: AsyncSession, battery_id: str, temperature_c: float, timestamp: datetime.datetime
) -> None:
    settings = get_settings()
    if temperature_c >= settings.HIGH_TEMPERATURE_CRITICAL_C:
        severity, threshold = SEVERITY_CRITICAL, settings.HIGH_TEMPERATURE_CRITICAL_C
    elif temperature_c >= settings.HIGH_TEMPERATURE_WARNING_C:
        severity, threshold = SEVERITY_WARNING, settings.HIGH_TEMPERATURE_WARNING_C
    else:
        await resolve_alert_if_active(
            session,
            battery_id=battery_id,
            alert_type=ALERT_TYPE_HIGH_TEMPERATURE,
            resolved_at=timestamp,
        )
        return

    await create_or_retain_alert(
        session,
        battery_id=battery_id,
        alert_type=ALERT_TYPE_HIGH_TEMPERATURE,
        severity=severity,
        message=f"{battery_id} temperature is {temperature_c:.1f}°C, at or above the "
        f"{severity.lower()} threshold of {threshold:.1f}°C",
        measured_value=temperature_c,
        threshold_value=threshold,
        timestamp=timestamp,
    )


async def _evaluate_voltage_anomaly(
    session: AsyncSession,
    battery_id: str,
    voltage: float,
    state_of_charge: float,
    nominal_voltage: float,
    timestamp: datetime.datetime,
) -> None:
    """Contract section 11's simplified expected-voltage model, compared
    against what this event actually reported (Contract section 25's
    "compare measured voltage against the simulator's expected voltage")."""
    settings = get_settings()
    expected_voltage = nominal_voltage * (0.9 + 0.2 * (state_of_charge / 100))
    if expected_voltage <= 0:
        # A misconfigured battery (nominal_voltage <= 0) has no meaningful
        # expected voltage to compare against -- skip rather than divide by
        # zero or report a nonsensical percentage.
        return
    deviation_percent = abs(voltage - expected_voltage) / expected_voltage * 100

    if deviation_percent >= settings.VOLTAGE_ANOMALY_CRITICAL_PERCENT:
        severity, threshold = SEVERITY_CRITICAL, settings.VOLTAGE_ANOMALY_CRITICAL_PERCENT
    elif deviation_percent >= settings.VOLTAGE_ANOMALY_WARNING_PERCENT:
        severity, threshold = SEVERITY_WARNING, settings.VOLTAGE_ANOMALY_WARNING_PERCENT
    else:
        await resolve_alert_if_active(
            session,
            battery_id=battery_id,
            alert_type=ALERT_TYPE_VOLTAGE_ANOMALY,
            resolved_at=timestamp,
        )
        return

    await create_or_retain_alert(
        session,
        battery_id=battery_id,
        alert_type=ALERT_TYPE_VOLTAGE_ANOMALY,
        severity=severity,
        message=f"{battery_id} voltage {voltage:.2f}V deviates {deviation_percent:.1f}% from "
        f"the expected {expected_voltage:.2f}V, at or above the {severity.lower()} "
        f"threshold of {threshold:.1f}%",
        measured_value=deviation_percent,
        threshold_value=threshold,
        timestamp=timestamp,
    )


async def _get_soc_at_or_before(
    session: AsyncSession, battery_id: str, cutoff: datetime.datetime
) -> float | None:
    query = (
        select(Telemetry.state_of_charge)
        .where(Telemetry.battery_id == battery_id)
        .where(Telemetry.timestamp <= cutoff)
        .order_by(Telemetry.timestamp.desc())
        .limit(1)
    )
    return await session.scalar(query)


async def _evaluate_rapid_discharge(
    session: AsyncSession, battery_id: str, state_of_charge: float, timestamp: datetime.datetime
) -> None:
    """Contract section 25: SOC decline over a rolling window, not a single
    reading -- needs a comparison point from `telemetry` history, unlike
    every other rule here which only looks at the current event.

    If this battery doesn't yet have a reading old enough to compare
    against (less than the window has passed since it started reporting),
    there simply isn't enough data to say anything either way -- this
    deliberately does neither `create_or_retain_alert` nor
    `resolve_alert_if_active` in that case, rather than guessing.
    """
    settings = get_settings()
    window = datetime.timedelta(minutes=settings.RAPID_DISCHARGE_WINDOW_MINUTES)
    soc_at_window_start = await _get_soc_at_or_before(session, battery_id, timestamp - window)
    if soc_at_window_start is None:
        return

    decline = soc_at_window_start - state_of_charge  # positive == SOC dropped

    if decline >= settings.RAPID_DISCHARGE_CRITICAL_PERCENTAGE_POINTS:
        severity, threshold = (
            SEVERITY_CRITICAL,
            settings.RAPID_DISCHARGE_CRITICAL_PERCENTAGE_POINTS,
        )
    elif decline >= settings.RAPID_DISCHARGE_WARNING_PERCENTAGE_POINTS:
        severity, threshold = (
            SEVERITY_WARNING,
            settings.RAPID_DISCHARGE_WARNING_PERCENTAGE_POINTS,
        )
    else:
        await resolve_alert_if_active(
            session,
            battery_id=battery_id,
            alert_type=ALERT_TYPE_RAPID_DISCHARGE,
            resolved_at=timestamp,
        )
        return

    await create_or_retain_alert(
        session,
        battery_id=battery_id,
        alert_type=ALERT_TYPE_RAPID_DISCHARGE,
        severity=severity,
        message=f"{battery_id} SOC dropped {decline:.1f} percentage points in the last "
        f"{settings.RAPID_DISCHARGE_WINDOW_MINUTES:.0f} minutes, at or above the "
        f"{severity.lower()} threshold of {threshold:.1f}pp",
        measured_value=decline,
        threshold_value=threshold,
        timestamp=timestamp,
    )


# --------------------------------------------------------------------------
# Entry point called from telemetry ingestion
# --------------------------------------------------------------------------


async def evaluate_rules_for_events(
    session: AsyncSession, events: Iterable[TelemetryEvent], batteries_by_id: dict[str, Battery]
) -> None:
    """Run every telemetry-triggered rule for a set of newly-accepted events.

    `events` should be the same "one newest event per battery" set
    `_upsert_current_state` (`app/services/telemetry_service.py`) already
    reduces a batch down to -- this function evaluates once per battery per
    request, using each battery's single newest accepted reading, the same
    way current-state itself only ever reflects the newest reading rather
    than every event in a batch (Contract section 18).

    `batteries_by_id` supplies each event's registered `Battery` row (for
    `nominal_voltage`, needed by the voltage-anomaly rule) -- passed in
    rather than queried again here, since the caller already has it from
    confirming the battery exists before accepting the event.

    `DEVICE_OFFLINE` is deliberately not evaluated here -- see this module's
    docstring; `app/services/offline_detector.py` raises it, and this
    function only ever *resolves* it, via
    `app/services/telemetry_service.py` calling `resolve_alert_if_active`
    directly once a previously-offline battery's current state is updated.
    """
    for event in events:
        battery = batteries_by_id.get(event.battery_id)
        if battery is None:
            continue  # defensive only -- ingestion already verified this battery exists

        await _evaluate_low_soc(session, event.battery_id, event.state_of_charge, event.timestamp)
        await _evaluate_high_temperature(
            session, event.battery_id, event.temperature_c, event.timestamp
        )
        await _evaluate_voltage_anomaly(
            session,
            event.battery_id,
            event.voltage,
            event.state_of_charge,
            battery.nominal_voltage,
            event.timestamp,
        )
        await _evaluate_rapid_discharge(
            session, event.battery_id, event.state_of_charge, event.timestamp
        )

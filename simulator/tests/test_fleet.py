"""Unit tests for the fleet coordinator (no server, no database, no real time)."""

from __future__ import annotations

import asyncio

from battery import BatteryStatus, ProfileType
from fleet import Fleet, FleetConfig, build_fleet


def run_and_collect(config: FleetConfig, max_ticks: int) -> list[dict]:
    """Run a fleet for `max_ticks` per battery and return every event produced."""
    events: list[dict] = []

    def on_event(event: dict) -> None:
        events.append(event)

    asyncio.run(Fleet(config).run(on_event, max_ticks=max_ticks))
    return events


def test_fleet_creates_requested_number_of_batteries():
    config = FleetConfig(device_count=7, fault_rate=0.0)
    batteries = build_fleet(config)
    assert len(batteries) == 7


def test_battery_ids_are_unique_and_correctly_formatted():
    batteries = build_fleet(FleetConfig(device_count=12))
    ids = [b.battery_id for b in batteries]
    assert len(set(ids)) == 12  # all unique
    for battery_id in ids:
        assert battery_id.startswith("BAT-")
        assert len(battery_id) == 10  # "BAT-" + 6 digits
        assert battery_id[4:].isdigit()


def test_fault_rate_zero_produces_no_faulty_batteries():
    batteries = build_fleet(FleetConfig(device_count=10, fault_rate=0.0))
    assert all(b.profile_type != ProfileType.FAULTY for b in batteries)


def test_fault_rate_one_produces_all_faulty_batteries():
    batteries = build_fleet(FleetConfig(device_count=10, fault_rate=1.0))
    assert all(b.profile_type == ProfileType.FAULTY for b in batteries)


def test_fault_rate_determines_faulty_count():
    batteries = build_fleet(FleetConfig(device_count=10, fault_rate=0.3))
    faulty_count = sum(1 for b in batteries if b.profile_type == ProfileType.FAULTY)
    assert faulty_count == 3


def test_non_faulty_batteries_get_a_mix_of_normal_profiles():
    batteries = build_fleet(FleetConfig(device_count=9, fault_rate=0.0))
    profiles_used = {b.profile_type for b in batteries}
    assert profiles_used == {
        ProfileType.RESIDENTIAL,
        ProfileType.SOLAR,
        ProfileType.COMMERCIAL,
    }


def test_invalid_device_count_raises():
    for bad_count in (0, -1):
        try:
            FleetConfig(device_count=bad_count)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for device_count={bad_count}")


def test_invalid_telemetry_interval_raises():
    for bad_interval in (0, -5.0):
        try:
            FleetConfig(telemetry_interval_seconds=bad_interval)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for interval={bad_interval}")


def test_invalid_fault_rate_raises():
    for bad_rate in (-0.1, 1.1):
        try:
            FleetConfig(fault_rate=bad_rate)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for fault_rate={bad_rate}")


def test_run_produces_exactly_devices_times_ticks_events():
    config = FleetConfig(device_count=3, telemetry_interval_seconds=0.01, random_seed=0)
    events = run_and_collect(config, max_ticks=4)
    assert len(events) == 3 * 4


def test_run_supports_an_async_callback():
    config = FleetConfig(device_count=2, telemetry_interval_seconds=0.01, random_seed=0)
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        await asyncio.sleep(0)  # simulates awaiting something, e.g. a future HTTP call
        events.append(event)

    asyncio.run(Fleet(config).run(on_event, max_ticks=2))
    assert len(events) == 2 * 2


def test_events_contain_all_required_telemetry_fields():
    config = FleetConfig(device_count=1, telemetry_interval_seconds=0.01, random_seed=0)
    events = run_and_collect(config, max_ticks=1)
    required_fields = {
        "event_id",
        "battery_id",
        "timestamp",
        "state_of_charge",
        "voltage",
        "current",
        "power_kw",
        "temperature_c",
        "health_percent",
        "status",
    }
    assert required_fields.issubset(events[0].keys())


def test_event_status_is_a_plain_string_not_an_enum():
    config = FleetConfig(device_count=1, telemetry_interval_seconds=0.01, random_seed=0)
    events = run_and_collect(config, max_ticks=1)
    assert isinstance(events[0]["status"], str)
    assert events[0]["status"] in {s.value for s in BatteryStatus}


def test_same_seed_produces_identical_fleet_behavior():
    config_a = FleetConfig(device_count=4, telemetry_interval_seconds=0.01, random_seed=99)
    config_b = FleetConfig(device_count=4, telemetry_interval_seconds=0.01, random_seed=99)
    events_a = run_and_collect(config_a, max_ticks=5)
    events_b = run_and_collect(config_b, max_ticks=5)

    # Timestamps will differ (real wall-clock), so compare everything else.
    def strip_timestamp(event: dict) -> dict:
        return {k: v for k, v in event.items() if k not in ("event_id", "timestamp")}

    assert [strip_timestamp(e) for e in events_a] == [strip_timestamp(e) for e in events_b]


def test_different_seeds_produce_different_noise():
    config_a = FleetConfig(device_count=2, telemetry_interval_seconds=0.01, random_seed=1)
    config_b = FleetConfig(device_count=2, telemetry_interval_seconds=0.01, random_seed=2)
    events_a = run_and_collect(config_a, max_ticks=3)
    events_b = run_and_collect(config_b, max_ticks=3)
    voltages_a = [e["voltage"] for e in events_a]
    voltages_b = [e["voltage"] for e in events_b]
    assert voltages_a != voltages_b


def test_zero_max_ticks_produces_no_events():
    config = FleetConfig(device_count=5, telemetry_interval_seconds=0.01, random_seed=0)
    events = run_and_collect(config, max_ticks=0)
    assert events == []

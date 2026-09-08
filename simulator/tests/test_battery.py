"""Unit tests for the Battery physics model (no server, no database)."""

from __future__ import annotations

from battery import Battery, BatteryStatus, ProfileType


def make_battery(**overrides) -> Battery:
    defaults = dict(
        battery_id="BAT-000001",
        capacity_kwh=10.0,
        max_power_kw=5.0,
        nominal_voltage=48.0,
        profile_type=ProfileType.RESIDENTIAL,
        state_of_charge=50.0,
        random_seed=0,
    )
    defaults.update(overrides)
    return Battery(**defaults)


def test_charging_increases_soc():
    battery = make_battery(state_of_charge=50.0)
    battery.tick(dt_seconds=3600, requested_power_kw=2.0)  # 1 hour at +2kW
    assert battery.state_of_charge > 50.0


def test_discharging_decreases_soc():
    battery = make_battery(state_of_charge=50.0)
    battery.tick(dt_seconds=3600, requested_power_kw=-2.0)
    assert battery.state_of_charge < 50.0


def test_soc_math_matches_contract_formula():
    # 10kWh battery at 50% = 5kWh stored. Charging at +2kW for 1 hour adds 2kWh
    # -> 7kWh stored -> 70% SOC exactly.
    battery = make_battery(capacity_kwh=10.0, state_of_charge=50.0)
    battery.tick(dt_seconds=3600, requested_power_kw=2.0)
    assert battery.state_of_charge == 70.0


def test_soc_never_exceeds_100():
    battery = make_battery(state_of_charge=99.0, capacity_kwh=10.0, max_power_kw=50.0)
    battery.tick(dt_seconds=3600, requested_power_kw=50.0)  # way more than needed
    assert battery.state_of_charge == 100.0


def test_soc_never_drops_below_0():
    battery = make_battery(state_of_charge=1.0, capacity_kwh=10.0, max_power_kw=50.0)
    battery.tick(dt_seconds=3600, requested_power_kw=-50.0)
    assert battery.state_of_charge == 0.0


def test_power_is_clamped_to_max_power_kw():
    battery = make_battery(max_power_kw=5.0, state_of_charge=50.0)
    battery.tick(dt_seconds=1, requested_power_kw=999.0)
    assert battery.power_kw == 5.0


def test_charging_stops_once_full():
    battery = make_battery(state_of_charge=100.0)
    battery.tick(dt_seconds=60, requested_power_kw=3.0)
    assert battery.power_kw == 0.0
    assert battery.state_of_charge == 100.0


def test_discharging_stops_once_empty():
    battery = make_battery(state_of_charge=0.0)
    battery.tick(dt_seconds=60, requested_power_kw=-3.0)
    assert battery.power_kw == 0.0
    assert battery.state_of_charge == 0.0


def test_status_reflects_charging_discharging_idle():
    battery = make_battery(state_of_charge=50.0)

    battery.tick(dt_seconds=60, requested_power_kw=2.0)
    assert battery.status == BatteryStatus.CHARGING

    battery.tick(dt_seconds=60, requested_power_kw=-2.0)
    assert battery.status == BatteryStatus.DISCHARGING

    battery.tick(dt_seconds=60, requested_power_kw=0.0)
    assert battery.status == BatteryStatus.IDLE


def test_current_is_derived_from_power_and_voltage():
    battery = make_battery(state_of_charge=50.0)
    battery.tick(dt_seconds=60, requested_power_kw=2.0)
    expected_current = (battery.power_kw * 1000.0) / battery.voltage
    assert battery.current == expected_current


def test_health_degrades_with_usage_and_stays_in_bounds():
    battery = make_battery(state_of_charge=50.0, capacity_kwh=10.0, max_power_kw=5.0)
    starting_health = battery.health_percent
    for _ in range(200):
        battery.tick(dt_seconds=60, requested_power_kw=5.0)
        battery.state_of_charge = 50.0  # pin SOC so we can keep discharging/charging
    assert battery.health_percent < starting_health
    assert 0.0 <= battery.health_percent <= 100.0


def test_same_seed_produces_identical_noise_sequence():
    a = make_battery(random_seed=42, state_of_charge=50.0)
    b = make_battery(random_seed=42, state_of_charge=50.0)
    for _ in range(10):
        a.tick(dt_seconds=60, requested_power_kw=1.5)
        b.tick(dt_seconds=60, requested_power_kw=1.5)
    assert a.voltage == b.voltage
    assert a.temperature_c == b.temperature_c


def test_voltage_rises_with_higher_soc():
    low = make_battery(state_of_charge=10.0, random_seed=1)
    high = make_battery(state_of_charge=90.0, random_seed=1)
    # No tick yet -- comparing the initial expected-voltage-from-SOC relationship.
    assert high.voltage > low.voltage

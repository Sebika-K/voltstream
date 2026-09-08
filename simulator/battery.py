"""Battery -- a single simulated battery's physical/electrical state.

This implements the state model and update formulas from the VoltStream
Implementation Contract (sections 8-13): the power convention, the SOC simulation,
the voltage simulation, the temperature simulation, and health degradation.

Deliberately scoped: this class knows nothing about usage profiles
(residential/solar/etc. -- that's `profiles.py`, a later step), fleets, or HTTP.
It only knows "how does one battery's physics evolve when something asks it to
charge or discharge" -- which is what lets us test it completely on its own,
with no server or database involved.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum


class ProfileType(str, Enum):
    """The four usage profiles defined in the Implementation Contract (section 6)."""

    RESIDENTIAL = "RESIDENTIAL"
    SOLAR = "SOLAR"
    COMMERCIAL = "COMMERCIAL"
    FAULTY = "FAULTY"


class BatteryStatus(str, Enum):
    """Status values a simulator is allowed to report (Contract section 7).

    OFFLINE is intentionally excluded here -- it's a backend-derived state caused
    by *missing* telemetry, never something the simulator itself claims about a
    battery that's actively reporting.
    """

    CHARGING = "CHARGING"
    DISCHARGING = "DISCHARGING"
    IDLE = "IDLE"
    FAULT = "FAULT"


# --- Simulation constants (Implementation Contract sections 11-13) ---

AMBIENT_TEMPERATURE_C_DEFAULT = 25.0
TEMPERATURE_RESPONSIVENESS = 0.10  # temperature closes 10% of the gap to target/update
TEMPERATURE_NOISE_C = 0.5  # +/- degrees C per update
VOLTAGE_NOISE_FRACTION = 0.01  # +/- 1% of expected voltage
HEALTH_DEGRADATION_PER_EFC = 0.02  # health percentage points lost per equivalent full cycle

# The Contract doesn't give an explicit numeric threshold for when power is "close
# enough to zero" to call the battery IDLE rather than CHARGING/DISCHARGING. We reuse
# MIN_DISCHARGE_POWER_KW's default (0.1 kW, Contract section 39 -- the prediction
# eligibility cutoff) rather than invent an unrelated second constant. This is a
# modeling choice, flagged here because the Contract doesn't state it directly.
IDLE_POWER_THRESHOLD_KW = 0.1


@dataclass
class Battery:
    """A single stateful simulated battery.

    Static fields (battery_id..longitude) are set once at creation and never change.
    Dynamic fields (state_of_charge..status) are the "current telemetry" and are
    mutated by `tick()`.
    """

    # --- static identity/configuration ---
    battery_id: str
    capacity_kwh: float
    max_power_kw: float
    nominal_voltage: float
    profile_type: ProfileType
    latitude: float | None = None
    longitude: float | None = None

    # --- dynamic state, mutated by tick() ---
    state_of_charge: float = 50.0  # percent, 0-100
    voltage: float = 0.0
    current: float = 0.0
    power_kw: float = 0.0
    temperature_c: float = AMBIENT_TEMPERATURE_C_DEFAULT
    health_percent: float = 100.0
    status: BatteryStatus = BatteryStatus.IDLE

    # --- simulation configuration, not part of the telemetry contract ---
    ambient_temperature_c: float = AMBIENT_TEMPERATURE_C_DEFAULT
    random_seed: int | None = None
    _rng: random.Random = field(default=None, repr=False)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self._rng is None:
            self._rng = random.Random(self.random_seed)
        # Start voltage consistent with the initial SOC rather than at 0.
        self.voltage = self._expected_voltage()

    def tick(self, dt_seconds: float, requested_power_kw: float) -> None:
        """Advance the battery's state by `dt_seconds`, attempting `requested_power_kw`.

        `requested_power_kw` is whatever the caller wants this step -- a usage
        profile, a test, fault injection later on. Positive charges, negative
        discharges (Contract section 8). The battery enforces the physical limits
        itself: it clamps to +/-max_power_kw, and cuts power to zero at the SOC
        boundaries (can't charge past 100%, can't discharge past 0%) -- the caller
        does not need to know or check those limits.
        """
        dt_hours = dt_seconds / 3600.0

        power_kw = self._apply_power_limits(requested_power_kw)
        self.power_kw = power_kw

        self._update_soc(power_kw, dt_hours)
        self.voltage = self._expected_voltage() + self._bounded_noise(
            VOLTAGE_NOISE_FRACTION * self._expected_voltage()
        )
        self.current = self._current_from_power(power_kw, self.voltage)
        self._update_temperature(power_kw)
        self._update_health(power_kw, dt_hours)
        self.status = self._derive_status(power_kw)

    # --- internals, one per formula in the Contract ---

    def _apply_power_limits(self, requested_power_kw: float) -> float:
        # Clamp to the battery's rated max power (Contract section 8).
        power_kw = max(-self.max_power_kw, min(self.max_power_kw, requested_power_kw))
        # At SOC=100%, charging (positive power) must stop; at SOC=0%, discharging
        # (negative power) must stop (Contract section 8).
        if self.state_of_charge >= 100.0 and power_kw > 0:
            power_kw = 0.0
        if self.state_of_charge <= 0.0 and power_kw < 0:
            power_kw = 0.0
        return power_kw

    def _update_soc(self, power_kw: float, dt_hours: float) -> None:
        # Contract section 9.
        stored_energy_kwh = self.capacity_kwh * self.state_of_charge / 100.0
        energy_change_kwh = power_kw * dt_hours
        new_energy_kwh = stored_energy_kwh + energy_change_kwh
        new_soc = (new_energy_kwh / self.capacity_kwh) * 100.0
        self.state_of_charge = max(0.0, min(100.0, new_soc))

    def _expected_voltage(self) -> float:
        # Contract section 11: a simplified model, not an electrochemical one.
        soc_fraction = self.state_of_charge / 100.0
        return self.nominal_voltage * (0.9 + 0.2 * soc_fraction)

    @staticmethod
    def _current_from_power(power_kw: float, voltage: float) -> float:
        # Contract section 8: current_A = (power_kw * 1000) / voltage_V.
        if voltage <= 0:
            return 0.0
        return (power_kw * 1000.0) / voltage

    def _update_temperature(self, power_kw: float) -> None:
        # Contract section 12.
        load_fraction = abs(power_kw) / self.max_power_kw if self.max_power_kw else 0.0
        target_temperature = self.ambient_temperature_c + (15.0 * load_fraction)
        self.temperature_c = (
            self.temperature_c
            + TEMPERATURE_RESPONSIVENESS * (target_temperature - self.temperature_c)
            + self._bounded_noise(TEMPERATURE_NOISE_C)
        )

    def _update_health(self, power_kw: float, dt_hours: float) -> None:
        # Contract section 13. Incremental (this tick's throughput only) so behavior
        # is correct regardless of what health_percent started at.
        energy_this_tick_kwh = abs(power_kw) * dt_hours
        efc_this_tick = energy_this_tick_kwh / (2 * self.capacity_kwh)
        degradation = HEALTH_DEGRADATION_PER_EFC * efc_this_tick
        self.health_percent = max(0.0, min(100.0, self.health_percent - degradation))

    def _derive_status(self, power_kw: float) -> BatteryStatus:
        if abs(power_kw) < IDLE_POWER_THRESHOLD_KW:
            return BatteryStatus.IDLE
        return BatteryStatus.CHARGING if power_kw > 0 else BatteryStatus.DISCHARGING

    def _bounded_noise(self, magnitude: float) -> float:
        return self._rng.uniform(-magnitude, magnitude)

"""fleet.py -- run many simulated batteries at once, concurrently, with asyncio.

`battery.py` (step 1) already knows how one battery evolves over time; 
`profiles.py` (step 2) already knows
how much power a usage profile wants at a given hour. This file's only job is
coordination: build a configurable group of batteries and advance all of them
concurrently, forever, on a timer -- using `asyncio` tasks (cheap, cooperative,
thousands can exist in one process) rather than OS threads (expensive, an OS
limit long before "thousands").

Still deliberately scoped: this module does not know about HTTP or the
backend. Each tick hands a plain telemetry-shaped dict to a callback the
caller supplies (`on_event`) -- wiring that callback up to an actual HTTP
POST is a later step (Roadmap 1.10), once the ingestion endpoint exists.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from battery import Battery, ProfileType
from profiles import requested_power_kw

# A callback may be a plain function or an async function -- run() awaits it
# either way. This is what lets a later step swap in "await http_client.post(...)"
# without changing anything in this file.
TelemetryCallback = Callable[[dict[str, Any]], "Awaitable[None] | None"]

# The Contract doesn't say how a fleet should divide up profile types when all
# you're given is one FAULT_RATE number -- it only defines what each profile
# does once assigned. We fill the non-faulty share evenly across the three
# normal profiles. This is a modeling choice, flagged because the Contract
# doesn't state it directly.
_NORMAL_PROFILES = (ProfileType.RESIDENTIAL, ProfileType.SOLAR, ProfileType.COMMERCIAL)


@dataclass
class FleetConfig:
    """The four knobs Roadmap 1.3 requires, plus the battery specs every
    device in the fleet is built with (the Contract has no per-battery
    provisioning story yet, so every device in a given fleet is identical
    hardware -- just running different profiles)."""

    device_count: int = 10
    telemetry_interval_seconds: float = 5.0
    fault_rate: float = 0.0  # fraction (0.0-1.0) of the fleet assigned the FAULTY profile
    random_seed: int | None = None
    capacity_kwh: float = 10.0
    max_power_kw: float = 5.0
    nominal_voltage: float = 48.0

    def __post_init__(self) -> None:
        if self.device_count < 1:
            raise ValueError(f"device_count must be >= 1, got {self.device_count}")
        if self.telemetry_interval_seconds <= 0:
            raise ValueError(
                "telemetry_interval_seconds must be > 0, "
                f"got {self.telemetry_interval_seconds}"
            )
        if not 0.0 <= self.fault_rate <= 1.0:
            raise ValueError(f"fault_rate must be 0.0-1.0, got {self.fault_rate}")


def _profile_for_index(index: int, device_count: int, fault_rate: float) -> ProfileType:
    """Decide device `index`'s profile out of `device_count` total devices.

    Deterministic by index (not randomized): the first `round(device_count *
    fault_rate)` devices are FAULTY, the rest cycle RESIDENTIAL/SOLAR/COMMERCIAL
    in order. That means fleet *composition* is the same every run for a given
    config -- `random_seed` governs noise and variation, not who is faulty.
    """
    fault_count = round(device_count * fault_rate)
    if index < fault_count:
        return ProfileType.FAULTY
    return _NORMAL_PROFILES[(index - fault_count) % len(_NORMAL_PROFILES)]


def build_fleet(config: FleetConfig) -> list[Battery]:
    """Create `config.device_count` batteries per `config`. Nothing runs yet --
    this is pure setup, so fleet composition can be tested with no event loop."""
    seed_source = random.Random(config.random_seed)
    batteries = []
    for index in range(config.device_count):
        profile_type = _profile_for_index(index, config.device_count, config.fault_rate)
        # Each battery gets its own seed derived from the fleet seed, so every
        # battery's noise sequence differs -- but the whole fleet is still
        # 100% reproducible from one `random_seed`.
        battery_seed = seed_source.randrange(2**32) if config.random_seed is not None else None
        batteries.append(
            Battery(
                battery_id=f"BAT-{index + 1:06d}",
                capacity_kwh=config.capacity_kwh,
                max_power_kw=config.max_power_kw,
                nominal_voltage=config.nominal_voltage,
                profile_type=profile_type,
                random_seed=battery_seed,
            )
        )
    return batteries


def _make_event(battery: Battery) -> dict[str, Any]:
    """Package a battery's current state as a telemetry event (Contract section 7's
    required fields). `event_id` is a fresh UUID v4 per event, exactly as the
    Contract requires: it's the producer's job to generate it before transmission."""
    return {
        "event_id": str(uuid.uuid4()),
        "battery_id": battery.battery_id,
        "timestamp": datetime.now(timezone.utc),
        "state_of_charge": battery.state_of_charge,
        "voltage": battery.voltage,
        "current": battery.current,
        "power_kw": battery.power_kw,
        "temperature_c": battery.temperature_c,
        "health_percent": battery.health_percent,
        "status": battery.status.value,
    }


async def _run_battery(
    battery: Battery,
    config: FleetConfig,
    profile_rng: random.Random,
    on_event: TelemetryCallback,
    max_ticks: int | None,
) -> None:
    """One battery's independent lifecycle for as long as the fleet runs.

    This coroutine spends almost all of its time suspended at `await
    asyncio.sleep(...)` -- it isn't consuming a thread or CPU while waiting.
    That's exactly what lets `Fleet.run()` start `device_count` of these
    concurrently in a single process without needing `device_count` OS threads.
    """
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        await asyncio.sleep(config.telemetry_interval_seconds)

        # Contract section 5: profile calculations may use a simulation-local
        # time; we use UTC wall-clock hour for that today (no separate
        # simulated-time-of-day clock exists yet), while the event's own
        # `timestamp` field stays real UTC as the Contract requires.
        hour_of_day = datetime.now(timezone.utc).hour
        power_kw = requested_power_kw(
            battery.profile_type, battery.max_power_kw, hour_of_day, profile_rng
        )
        battery.tick(dt_seconds=config.telemetry_interval_seconds, requested_power_kw=power_kw)

        result = on_event(_make_event(battery))
        if asyncio.iscoroutine(result):
            await result

        ticks += 1


class Fleet:
    """A configurable group of batteries that tick concurrently via asyncio.

    Construction only builds the batteries (see `build_fleet`) -- nothing runs
    until `run()` is awaited. Splitting it this way means fleet composition
    (how many devices, which profiles) can be inspected and tested with no
    event loop involved at all.
    """

    def __init__(self, config: FleetConfig) -> None:
        self.config = config
        self.batteries: list[Battery] = build_fleet(config)
        # Separate RNG per battery for *profile* variation, independent of each
        # battery's own internal noise RNG -- so profile randomness and physics
        # noise never draw from (and disturb) the same sequence.
        seed_source = random.Random(config.random_seed)
        self._profile_rngs = [
            random.Random(seed_source.randrange(2**32) if config.random_seed is not None else None)
            for _ in self.batteries
        ]

    async def run(self, on_event: TelemetryCallback, *, max_ticks: int | None = None) -> None:
        """Run every battery concurrently.

        Each battery ticks once every `telemetry_interval_seconds`, forever, or
        `max_ticks` times if given (tests use a small `max_ticks`; a real
        deployment calls `asyncio.run(fleet.run(callback))` with no `max_ticks`
        and stops it from outside, e.g. on shutdown).

        `on_event` fires once per battery per tick with that tick's telemetry
        event (a plain dict, shaped per Contract section 7). It can be a plain
        function or an `async def` -- both work, which is what will let a later
        step pass in something that awaits an HTTP POST without this file
        changing at all.
        """
        await asyncio.gather(
            *(
                _run_battery(battery, self.config, profile_rng, on_event, max_ticks)
                for battery, profile_rng in zip(self.batteries, self._profile_rngs)
            )
        )


if __name__ == "__main__":
    # A small manual demo: `python3 fleet.py` runs a 5-device fleet for 3 ticks
    # each and prints every event, so you can see it working without writing a
    # test. Roadmap 1.3 calls for validating progressively at 10/100/1,000
    # devices -- try `python3 fleet.py --device-count 100`.
    import argparse

    parser = argparse.ArgumentParser(description="Run a VoltStream battery fleet.")
    parser.add_argument("--device-count", type=int, default=5)
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between ticks")
    parser.add_argument("--fault-rate", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-ticks", type=int, default=3, help="per battery; omit=forever")
    args = parser.parse_args()

    demo_config = FleetConfig(
        device_count=args.device_count,
        telemetry_interval_seconds=args.interval,
        fault_rate=args.fault_rate,
        random_seed=args.seed,
    )

    def print_event(event: dict[str, Any]) -> None:
        print(
            f"{event['timestamp'].isoformat()}  {event['battery_id']}  "
            f"{event['status']:<12} SOC={event['state_of_charge']:6.2f}%  "
            f"P={event['power_kw']:+6.2f}kW  T={event['temperature_c']:5.1f}C"
        )

    print(f"Running {args.device_count} devices, fault_rate={args.fault_rate} ...")
    asyncio.run(Fleet(demo_config).run(print_event, max_ticks=args.max_ticks))

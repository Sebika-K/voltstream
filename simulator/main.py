"""simulator/main.py -- the real entrypoint: build a fleet, register it with
the backend, then continuously batch and POST its telemetry (Roadmap 1.10).

`fleet.py`'s own `__main__` block is a no-network demo for eyeballing battery
behavior locally (it just prints events). This is the actual "device -> HTTP
-> backend" path the TDD's architecture diagram and the Roadmap describe.

Run with (backend already running and reachable at BACKEND_URL):

    cd simulator
    python3 main.py
"""

from __future__ import annotations

import asyncio
import logging

from batching import BatchAccumulator
from battery import Battery
from client import BackendClient, BatchDeliveryError
from config import SimulatorConfig
from fleet import Fleet, FleetConfig
from logging_config import configure_logging

logger = logging.getLogger("simulator")


async def register_fleet(client: BackendClient, batteries: list[Battery]) -> None:
    """Register every battery before any telemetry is sent for it.

    Sequential rather than concurrent on purpose: this only runs once at
    startup (device_count calls total, not device_count-per-tick), and doing
    it one at a time keeps a registration failure's error unambiguous about
    exactly which battery caused it -- worth the small extra startup time for
    a step this infrequent.
    """
    for battery in batteries:
        await client.register_battery(battery)
        logger.info(
            "battery_registered",
            extra={"battery_id": battery.battery_id, "profile_type": battery.profile_type.value},
        )


async def run_simulator(
    config: SimulatorConfig, client: BackendClient, *, max_ticks: int | None = None
) -> None:
    """Wire fleet generation, batching, and the HTTP client together and run:
    create fleet -> register batteries -> begin telemetry (Roadmap 1.6's
    simulator startup flow), then generate events -> accumulate batch -> POST
    batch -> repeat (TDD section 7's simulator pipeline).

    Runs forever unless `max_ticks` is given -- production use (`main()`
    below) never sets it; tests pass a small number for the same reason
    `Fleet.run()` itself supports `max_ticks` (see fleet.py).

    Takes an already-constructed `BackendClient` rather than a bare URL so
    tests can inject a fake client -- `main()` is what builds the real one
    for an actual run.
    """
    fleet_config = FleetConfig(
        device_count=config.device_count,
        telemetry_interval_seconds=config.telemetry_interval_seconds,
        fault_rate=config.fault_rate,
        random_seed=config.random_seed,
    )
    fleet = Fleet(fleet_config)

    await register_fleet(client, fleet.batteries)
    logger.info("fleet_registered", extra={"battery_count": len(fleet.batteries)})

    async def send(events: list[dict]) -> None:
        # The client logs each batch itself (`batch_sent` / `batch_failed`), because
        # it is the one that knows the request ID, status and duration.
        try:
            await client.send_batch(events)
        except BatchDeliveryError:
            # Retries ran out (Roadmap 5.3). The client already logged
            # `batch_dropped` with the details. The simulation stays alive and
            # carries on with the next batch: one lost batch must not end the run.
            # (A refusal the backend will never accept -- e.g. 404 -- is NOT caught
            # here on purpose; that still stops the process loudly, and Docker's
            # restart re-registers the fleet, which fixes the usual cause.)
            pass

    accumulator = BatchAccumulator(config.batch_size, send)

    async def on_event(event: dict) -> None:
        await accumulator.add(event)

    try:
        await fleet.run(on_event, max_ticks=max_ticks)
    finally:
        # The fleet may stop (cancellation, or a finite max_ticks in a test)
        # with a partial batch still sitting in the accumulator -- without
        # this, those last few events would simply never be sent.
        await accumulator.flush()


def main() -> None:
    config = SimulatorConfig.from_env()
    configure_logging(level=config.log_level, log_format=config.log_format, service="simulator")

    async def run() -> None:
        async with BackendClient(config.backend_url, retry_policy=config.retry_policy) as client:
            await run_simulator(config, client)

    asyncio.run(run())


if __name__ == "__main__":
    main()

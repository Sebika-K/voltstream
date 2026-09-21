"""Tests for main.py's orchestration (Roadmap 1.10): registering the fleet
before sending any telemetry, and wiring the fleet's events through batching
to the client.

Uses a fully fake in-memory client (no real HTTP, no real backend), so these
test the WIRING -- registration-before-telemetry ordering, batching, and the
final flush -- not `BackendClient`'s own HTTP behavior (test_client.py) or
`BatchAccumulator`'s own flushing logic (test_batching.py).
"""

from __future__ import annotations

import asyncio

from battery import Battery
from client import BatchDeliveryError
from config import SimulatorConfig
from fleet import build_fleet, FleetConfig
from main import register_fleet, run_simulator


class FakeClient:
    """Records what main.py's orchestration sends, without any real HTTP."""

    def __init__(self) -> None:
        self.registered: list[str] = []
        self.batches_sent: list[list[dict]] = []

    async def register_battery(self, battery: Battery) -> None:
        self.registered.append(battery.battery_id)

    async def send_batch(self, events: list[dict]) -> dict:
        self.batches_sent.append(events)
        return {"received": len(events), "inserted": len(events), "duplicates": 0}


def test_register_fleet_registers_every_battery_exactly_once():
    batteries = build_fleet(FleetConfig(device_count=5, random_seed=0))
    client = FakeClient()

    asyncio.run(register_fleet(client, batteries))

    assert client.registered == [b.battery_id for b in batteries]
    assert len(set(client.registered)) == 5


def test_run_simulator_registers_all_batteries_before_any_batch_is_sent():
    config = SimulatorConfig(
        device_count=3, telemetry_interval_seconds=0.01, batch_size=100, random_seed=0
    )
    client = FakeClient()

    # batch_size=100 with only 3 devices x 2 ticks = 6 events total means no
    # batch reaches the threshold -- so if `batches_sent` is non-empty here at
    # all, it can only be the final flush, which happens strictly after
    # registration in run_simulator's implementation.
    asyncio.run(run_simulator(config, client, max_ticks=2))

    assert len(client.registered) == 3
    assert sum(len(batch) for batch in client.batches_sent) == 6


def test_run_simulator_sends_a_batch_as_soon_as_it_fills():
    config = SimulatorConfig(
        device_count=2, telemetry_interval_seconds=0.01, batch_size=2, random_seed=0
    )
    client = FakeClient()

    # 2 devices x 3 ticks = 6 events, batch_size=2 -> exactly 3 full batches,
    # each sent as soon as it fills, with nothing left for a final flush.
    asyncio.run(run_simulator(config, client, max_ticks=3))

    assert len(client.batches_sent) == 3
    assert all(len(batch) == 2 for batch in client.batches_sent)


def test_run_simulator_flushes_a_trailing_partial_batch_when_the_fleet_stops():
    config = SimulatorConfig(
        device_count=1, telemetry_interval_seconds=0.01, batch_size=10, random_seed=0
    )
    client = FakeClient()

    # 1 device x 4 ticks = 4 events, well short of batch_size=10 -- without
    # the final flush in run_simulator's `finally` block, these would never
    # be sent at all.
    asyncio.run(run_simulator(config, client, max_ticks=4))

    assert len(client.batches_sent) == 1  # exactly one flush, no threshold-triggered sends
    assert len(client.batches_sent[0]) == 4


def test_run_simulator_with_zero_ticks_still_registers_but_sends_nothing():
    config = SimulatorConfig(
        device_count=4, telemetry_interval_seconds=0.01, batch_size=10, random_seed=0
    )
    client = FakeClient()

    asyncio.run(run_simulator(config, client, max_ticks=0))

    assert len(client.registered) == 4
    assert client.batches_sent == []


class FlakyClient(FakeClient):
    """Fails the first batch the way BackendClient does once retries run out."""

    async def send_batch(self, events: list[dict]) -> dict:
        if not self.batches_sent:
            self.batches_sent.append(events)
            raise BatchDeliveryError(event_count=len(events), attempts=3, last_error=RuntimeError("down"))
        return await super().send_batch(events)


def test_a_dropped_batch_does_not_stop_the_simulator():
    config = SimulatorConfig(
        device_count=2, telemetry_interval_seconds=0.01, batch_size=2, random_seed=0
    )
    client = FlakyClient()

    asyncio.run(run_simulator(config, client, max_ticks=3))

    # The first batch was dropped, yet the other two were still sent.
    assert len(client.batches_sent) == 3

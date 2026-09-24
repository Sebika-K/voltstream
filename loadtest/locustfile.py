"""Locust load-test harness for VoltStream (Roadmap 6.1, Contract section 45).

Targets `POST /api/v1/telemetry/batch` -- the endpoint the Roadmap names
explicitly, and the one real device traffic actually hits (the simulator
never calls single-event ingestion in production use).

WHY A DEDICATED BATTERY POOL, NOT THE SIMULATOR'S OWN BATTERIES:
This harness registers its own batteries (BAT-950000 and up -- a range no
other part of the project uses) rather than reusing the 100 the simulator
registers. Two reasons: the harness needs to run its own repeatable
workload independent of whatever the simulator happens to be doing, and
having both write to the same batteries at once would make every
`battery_current_state` row lock-contended between two unrelated traffic
sources, which would measure that contention instead of the thing we
actually want to measure. Contract section 45's own load-test setup
(Locust hitting the batch endpoint) implies isolated, controlled traffic
for exactly this reason -- see the README for the recommended
`docker compose stop simulator` step before a real benchmark run.

HOW THROUGHPUT IS CONTROLLED:
Each simulated user sends one full batch per request with no pause
(`wait_time = constant(0)`), so total throughput is governed by Locust's
own `--users` / `--spawn-rate` CLI flags, not by anything in this file --
exactly the point of a load-test harness ("Done When: the same workload
can be run repeatedly" -- Roadmap 6.1). The number Locust itself reports
as requests/sec is batches/sec; multiply by BATCH_SIZE for events/sec.

Every event gets its own random `event_id`, so every request does a real
insert -- reusing IDs would let `ON CONFLICT DO NOTHING` make ingestion
look artificially cheap, which is not what real (non-retried) traffic
looks like.
"""

from __future__ import annotations

import os
import random
import uuid
from datetime import datetime, timezone

import requests
from locust import HttpUser, task, constant, events
from locust.runners import WorkerRunner

# Roadmap 5.3's simulator default batch size is 100 (Contract section 16's
# "default simulator batch size") -- matching it here means a first run with
# no configuration reflects real production traffic shape. Override with the
# environment variable to test other batch sizes later (Roadmap 6.4).
BATCH_SIZE = int(os.environ.get("LOADTEST_BATCH_SIZE", "100"))

# How many distinct batteries this harness's traffic is spread across. Bigger
# than BATCH_SIZE so a single batch rarely repeats a battery_id, and bigger
# than one Locust user's worth of batches so concurrent requests from
# different users don't all fight over the same `battery_current_state` rows
# (see the module docstring).
POOL_SIZE = int(os.environ.get("LOADTEST_BATTERY_POOL_SIZE", "200"))

BATTERY_ID_PREFIX = "BAT-95"  # a range no other part of the project uses (900-919 is used by backend test suites)
BATTERY_POOL: list[str] = [f"{BATTERY_ID_PREFIX}{n:04d}" for n in range(POOL_SIZE)]  # 4 digits so POOL_SIZE can go up to 9999 and still fit BAT-######

STATUSES = ["CHARGING", "DISCHARGING", "IDLE", "FAULT"]


@events.test_start.add_listener
def register_battery_pool(environment, **kwargs) -> None:
    """Register every pool battery once, before any load is generated.

    Runs on the master (or the only process, in the common single-machine
    case) -- `WorkerRunner` means this is a worker in a distributed Locust
    run, which must not repeat the registration a second time.
    """
    if isinstance(environment.runner, WorkerRunner):
        return

    host = environment.host
    if not host:
        raise SystemExit(
            "No --host given. Run e.g.: locust -f locustfile.py --host http://localhost:8000"
        )

    print(f"Registering {POOL_SIZE} load-test batteries ({BATTERY_ID_PREFIX}xxx) against {host} ...")
    registered = 0
    with requests.Session() as session:
        for battery_id in BATTERY_POOL:
            response = session.post(
                f"{host}/api/v1/batteries",
                json={
                    "battery_id": battery_id,
                    "capacity_kwh": 10.0,
                    "max_power_kw": 5.0,
                    "nominal_voltage": 48.0,
                    "profile_type": "RESIDENTIAL",
                },
                timeout=10,
            )
            # 201 = newly created, 200 = already registered with the same specs
            # (Contract section 6's idempotent re-registration) -- both are fine
            # on a second run of this same harness. Anything else is a real
            # problem (e.g. 409: some OTHER test left BAT-950xxx IDs behind
            # with different specs) and should stop the run, not run a
            # benchmark against a half-registered pool.
            if response.status_code not in (200, 201):
                raise SystemExit(
                    f"Registering {battery_id} failed: {response.status_code} {response.text[:300]}"
                )
            registered += 1
    print(f"Registered/confirmed {registered}/{POOL_SIZE} load-test batteries. Starting load.")


def _random_event(battery_id: str) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "battery_id": battery_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state_of_charge": round(random.uniform(0, 100), 2),
        "voltage": round(random.uniform(44.0, 52.0), 2),
        "current": round(random.uniform(-50.0, 50.0), 2),
        "power_kw": round(random.uniform(-5.0, 5.0), 2),
        "temperature_c": round(random.uniform(15.0, 35.0), 2),
        "health_percent": round(random.uniform(85.0, 100.0), 2),
        "status": random.choice(STATUSES),
    }


class TelemetryBatchUser(HttpUser):
    """One simulated device-fleet client: repeatedly POSTs a full batch of
    telemetry with no pause between requests. How many of these run at once
    (`--users`) and how fast they spin up (`--spawn-rate`) is what actually
    sets the load -- see the README for example commands."""

    wait_time = constant(0)

    @task
    def send_batch(self) -> None:
        payload = {"events": [_random_event(random.choice(BATTERY_POOL)) for _ in range(BATCH_SIZE)]}
        with self.client.post(
            "/api/v1/telemetry/batch",
            json=payload,
            name="/api/v1/telemetry/batch",
            catch_response=True,
        ) as response:
            if response.status_code != 201:
                response.failure(f"unexpected status {response.status_code}: {response.text[:200]}")

"""HTTP client wiring the simulator to the backend (Roadmap 1.10).

Everything the fleet produces is a plain dict shaped per Contract section 7
(and Battery objects for registration) -- this module is where that becomes
actual HTTP traffic against the endpoints Phase 1 already built:
`POST /api/v1/batteries` and `POST /api/v1/telemetry/batch`.

Deliberately narrow, matching this step's scope: no retry/backoff (that's
Roadmap 5.3) and no bounded queue/backpressure (Roadmap 5.4) live here yet --
those are their own later roadmap items, not part of "wire the simulator up
to the backend for the first time."
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import httpx

from battery import Battery
from retry import RetryPolicy, is_retryable

logger = logging.getLogger("simulator.client")


class BatchDeliveryError(Exception):
    """A batch could not be delivered even after every allowed attempt.

    Raised only for failures that *were* worth retrying (backend unreachable, slow,
    or reporting server trouble) and kept failing until the retry policy ran out.
    The client has already logged it as `batch_dropped`; the caller decides what a
    lost batch means (the simulator just carries on with the next one).
    """

    def __init__(self, *, event_count: int, attempts: int, last_error: BaseException) -> None:
        self.event_count = event_count
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"batch of {event_count} events not delivered after {attempts} attempts: {last_error}"
        )


def _event_to_json_safe(event: dict[str, Any]) -> dict[str, Any]:
    """Turn one fleet-produced event dict into something httpx can serialize.

    The only field that isn't already JSON-safe is `timestamp`: `fleet.py`
    deliberately keeps it as a real `datetime` object (so other callers, like
    the demo script's `print_event`, can call `.isoformat()` or do date math
    on it themselves) rather than pre-stringifying it for every consumer.
    That conversion happens here instead, right at the HTTP boundary, using
    the ISO-8601-with-timezone format the Contract's time contract (section
    5) requires and that the backend's schema already expects.
    """
    converted = dict(event)
    timestamp = converted.get("timestamp")
    if isinstance(timestamp, datetime):
        converted["timestamp"] = timestamp.isoformat()
    return converted


class BackendClient:
    """A thin async wrapper around one shared `httpx.AsyncClient` (TDD section
    7: "a shared httpx.AsyncClient", not one client per battery or per
    request -- connections get pooled and reused across every call this
    makes).

    Use as an async context manager so the underlying client is always
    closed:

        async with BackendClient("http://localhost:8000") as client:
            ...

    An existing `httpx.AsyncClient` can be injected instead (`http_client=`)
    -- this is how tests point the client at an in-memory fake transport
    instead of a real network call, without changing anything else about how
    `BackendClient` is used.
    """

    def __init__(
        self,
        base_url: str,
        http_client: httpx.AsyncClient | None = None,
        *,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        # `sleep` and `rng` are injectable so tests can run a whole retry sequence
        # instantly and with known "random" delays, instead of really waiting.
        self._retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(base_url=base_url.rstrip("/"))

    async def __aenter__(self) -> BackendClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        # Only close a client we created ourselves -- an injected client's
        # lifecycle belongs to whoever constructed it (a test, typically).
        if self._owns_client:
            await self._client.aclose()

    async def register_battery(self, battery: Battery) -> httpx.Response:
        """POST /api/v1/batteries for one battery.

        This is Roadmap 1.6's simulator startup flow (create fleet -> register
        batteries -> begin telemetry), now actually wired up. Registration
        itself is idempotent for equivalent metadata (Contract section 6), so
        calling this again for a battery already registered with the exact
        same specs is safe -- which matters because the simulator has no
        other way of knowing whether an earlier run already registered this
        fleet. A *conflicting* re-registration (same battery_id, different
        specs) raises via `raise_for_status()` -- there's no silent-skip or
        retry here, deliberately: that situation means the fleet config
        changed without resetting the database, which is worth surfacing
        loudly rather than papering over.
        """
        payload = {
            "battery_id": battery.battery_id,
            "capacity_kwh": battery.capacity_kwh,
            "max_power_kw": battery.max_power_kw,
            "nominal_voltage": battery.nominal_voltage,
            "latitude": battery.latitude,
            "longitude": battery.longitude,
            "profile_type": battery.profile_type.value,
        }
        response = await self._client.post("/api/v1/batteries", json=payload)
        response.raise_for_status()
        return response

    async def send_batch(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """POST /api/v1/telemetry/batch with a list of fleet-produced event dicts.

        Returns the parsed `{"received", "inserted", "duplicates"}` body
        (Contract section 16) on success.

        Temporary failures are retried with exponential backoff and jitter
        (Roadmap 5.3, see `retry.py`). If they persist through every allowed
        attempt this raises `BatchDeliveryError`. A failure that retrying cannot
        fix (a 4xx the backend deliberately refused) raises the original
        `httpx.HTTPStatusError` immediately.
        """
        if not events:
            return {"received": 0, "inserted": 0, "duplicates": 0}
        payload = {"events": [_event_to_json_safe(event) for event in events]}

        # This batch's ID (Roadmap 5.2), sent as `X-Request-ID`. The backend reuses a
        # well-formed incoming ID, so the same value appears in this service's
        # `batch_sent` line AND in the backend's `request_completed` /
        # `telemetry_batch_processed` lines. It stays the SAME across retries of the
        # same batch (Roadmap 5.3), so searching the logs for one ID shows every
        # attempt -- including a retry the backend recognised as a duplicate.
        request_id = uuid.uuid4().hex
        policy = self._retry_policy
        started = time.perf_counter()

        # `payload` was built once, above: every attempt sends the exact same events
        # with the exact same `event_id`s. That is what makes retrying safe -- the
        # backend ignores an event_id it has already stored (Roadmap 1.8).
        for attempt in range(1, policy.max_attempts + 1):
            try:
                response = await self._client.post(
                    "/api/v1/telemetry/batch", json=payload, headers={"X-Request-ID": request_id}
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                # Covers both "the backend answered with an error status" and "the
                # backend couldn't be reached at all".
                retryable = is_retryable(exc)
                will_retry = retryable and attempt < policy.max_attempts
                delay = policy.delay_for(attempt, self._rng) if will_retry else None
                # WARNING when we are going to try again (expected, temporary);
                # ERROR when this failure is final.
                logger.log(
                    logging.WARNING if will_retry else logging.ERROR,
                    "batch_failed",
                    extra={
                        "request_id": request_id,
                        "event_count": len(events),
                        "attempt": attempt,
                        "max_attempts": policy.max_attempts,
                        "status_code": (
                            exc.response.status_code
                            if isinstance(exc, httpx.HTTPStatusError)
                            else None
                        ),
                        "error": f"{type(exc).__name__}: {exc}",
                        "will_retry": will_retry,
                        "retry_in_seconds": None if delay is None else round(delay, 3),
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    },
                )
                if not retryable:
                    # The backend understood and refused (e.g. 404, 422): resending
                    # the same request can't help. Raise the original error so the
                    # problem is noticed loudly.
                    raise
                if not will_retry:
                    # Contract section 50: when an outage outlasts the retry policy,
                    # say so plainly rather than silently claim lossless delivery.
                    logger.error(
                        "batch_dropped",
                        extra={
                            "request_id": request_id,
                            "event_count": len(events),
                            "attempts": attempt,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    raise BatchDeliveryError(
                        event_count=len(events), attempts=attempt, last_error=exc
                    ) from exc
                await self._sleep(delay)
                continue

            result = response.json()
            logger.info(
                "batch_sent",
                extra={
                    "request_id": request_id,
                    "status_code": response.status_code,
                    "attempt": attempt,
                    "received": result["received"],
                    "inserted": result["inserted"],
                    "duplicates": result["duplicates"],
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            return result

        raise AssertionError("unreachable: the loop above always returns or raises")

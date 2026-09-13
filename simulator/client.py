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

from datetime import datetime
from typing import Any

import httpx

from battery import Battery


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

    def __init__(self, base_url: str, http_client: httpx.AsyncClient | None = None) -> None:
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
        (Contract section 16) on success. Raises `httpx.HTTPStatusError` on
        any non-2xx response -- there's no retry here yet (Roadmap 5.3 adds
        that); for this step, a failed send is the caller's problem to notice
        and log.
        """
        if not events:
            return {"received": 0, "inserted": 0, "duplicates": 0}
        payload = {"events": [_event_to_json_safe(event) for event in events]}
        response = await self._client.post("/api/v1/telemetry/batch", json=payload)
        response.raise_for_status()
        return response.json()

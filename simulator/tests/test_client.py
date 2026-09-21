"""Unit tests for BackendClient (Roadmap 1.10).

No real backend or network involved -- `httpx.MockTransport` intercepts every
request in-process, so these check exactly what `BackendClient` sends and how
it handles what comes back, the same way `test_fleet.py` tests fleet
coordination with no server or database.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from battery import Battery, ProfileType
from client import BackendClient, BatchDeliveryError
from retry import RetryPolicy


def make_battery(**overrides) -> Battery:
    defaults = dict(
        battery_id="BAT-000001",
        capacity_kwh=10.0,
        max_power_kw=5.0,
        nominal_voltage=48.0,
        profile_type=ProfileType.RESIDENTIAL,
        latitude=37.7749,
        longitude=-122.4194,
    )
    defaults.update(overrides)
    return Battery(**defaults)


def client_with_handler(handler, **kwargs) -> BackendClient:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://testserver"
    )
    return BackendClient("http://testserver", http_client=http_client, **kwargs)


def test_register_battery_posts_the_expected_payload():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["json"] = json.loads(request.content)
        return httpx.Response(201, json={"battery_id": "BAT-000001"})

    async def scenario():
        client = client_with_handler(handler)
        await client.register_battery(make_battery())
        await client.aclose()

    asyncio.run(scenario())

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/batteries"
    assert captured["json"] == {
        "battery_id": "BAT-000001",
        "capacity_kwh": 10.0,
        "max_power_kw": 5.0,
        "nominal_voltage": 48.0,
        "latitude": 37.7749,
        "longitude": -122.4194,
        "profile_type": "RESIDENTIAL",
    }


def test_register_battery_raises_on_a_conflicting_registration():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"error": {"code": "BATTERY_CONFLICT", "message": "mismatch"}}
        )

    async def scenario():
        client = client_with_handler(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await client.register_battery(make_battery())
        await client.aclose()

    asyncio.run(scenario())


def test_register_battery_succeeds_on_an_idempotent_replay():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"battery_id": "BAT-000001"})

    async def scenario():
        client = client_with_handler(handler)
        response = await client.register_battery(make_battery())
        assert response.status_code == 200
        await client.aclose()

    asyncio.run(scenario())


def test_send_batch_converts_datetime_timestamps_to_iso_strings():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(201, json={"received": 1, "inserted": 1, "duplicates": 0})

    event = {
        "event_id": "11111111-1111-4111-8111-111111111111",
        "battery_id": "BAT-000001",
        "timestamp": datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc),
        "state_of_charge": 55.0,
        "voltage": 49.0,
        "current": 10.0,
        "power_kw": 1.0,
        "temperature_c": 26.0,
        "health_percent": 100.0,
        "status": "CHARGING",
    }

    async def scenario():
        client = client_with_handler(handler)
        result = await client.send_batch([event])
        await client.aclose()
        return result

    result = asyncio.run(scenario())

    assert captured["json"]["events"][0]["timestamp"] == "2026-09-13T12:00:00+00:00"
    assert result == {"received": 1, "inserted": 1, "duplicates": 0}


def test_send_batch_with_no_events_makes_no_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201, json={"received": 0, "inserted": 0, "duplicates": 0})

    async def scenario():
        client = client_with_handler(handler)
        result = await client.send_batch([])
        await client.aclose()
        return result

    result = asyncio.run(scenario())

    assert calls == []
    assert result == {"received": 0, "inserted": 0, "duplicates": 0}


def test_send_batch_raises_when_a_server_error_exhausts_its_attempts():
    # 500 is retryable (Roadmap 5.3), so with a single allowed attempt the
    # failure surfaces as BatchDeliveryError (the retry behaviour itself is
    # tested in test_client_retry.py).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"code": "INTERNAL", "message": "boom"}})

    async def scenario():
        client = client_with_handler(handler, retry_policy=RetryPolicy(max_attempts=1))
        with pytest.raises(BatchDeliveryError):
            await client.send_batch([{"timestamp": datetime.now(timezone.utc)}])
        await client.aclose()

    asyncio.run(scenario())


def test_context_manager_closes_a_client_it_created_itself():
    async def scenario():
        async with BackendClient("http://testserver") as client:
            internal_client = client._client  # noqa: SLF001 -- lifecycle check only
        return internal_client

    internal_client = asyncio.run(scenario())
    assert internal_client.is_closed


def test_context_manager_leaves_an_injected_client_open():
    injected = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        base_url="http://testserver",
    )

    async def scenario():
        async with BackendClient("http://testserver", http_client=injected):
            pass

    asyncio.run(scenario())
    assert not injected.is_closed
    asyncio.run(injected.aclose())  # BackendClient didn't own it, so clean up ourselves

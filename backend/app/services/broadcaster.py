"""In-process real-time event broadcaster (Roadmap 3.1, Contract sections 34 & 36).

This is the "in-process broadcaster" the Contract explicitly allows for the
initial single-Uvicorn-worker architecture (section 36): every connected SSE
client gets its own `asyncio.Queue`, and publishing an event means pushing it
onto every currently-connected client's queue. There is no process boundary
to cross and no external system (Redis, Kafka, Postgres LISTEN/NOTIFY)
involved -- this only works because everything lives in one Python process.
If VoltStream ever moves to multiple Uvicorn workers, this class stops being
correct (each worker would have its own, disconnected set of subscribers),
and the Contract requires a cross-process mechanism before that's allowed to
happen (section 63).

Bounded per-client queues are the backpressure mechanism the Contract asks
for (section 36: "slow SSE clients MUST NOT cause unbounded server memory
growth"). A client that isn't reading fast enough (a stalled browser tab, a
connection the server hasn't noticed died yet) has its queue fill up; once
full, the oldest queued event is dropped to make room for the new one,
rather than growing the queue forever. Losing an old update for a slow
client is an acceptable trade the Contract already makes at a higher level:
section 35 states V1 does not guarantee replay of every missed SSE message,
and a REST refetch always restores correctness.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# How many un-delivered events a single slow client is allowed to accumulate
# before old ones start being dropped. At ~1 event/sec (Contract section
# 36), this is a comfortable ~30 seconds of buffering.
_CLIENT_QUEUE_MAXSIZE = 32


class Broadcaster:
    """Fan-out of realtime events to every currently-connected SSE client."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        """Register a new client and return the queue it should read events from.

        Called once per incoming SSE connection (see `app/api/stream.py`).
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=_CLIENT_QUEUE_MAXSIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove a client's queue, e.g. once its SSE connection closes."""
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        """How many clients are currently connected.

        Used by the periodic publisher (`app/services/realtime_publisher.py`)
        to skip doing aggregation work -- e.g. querying the fleet summary --
        when nobody is listening.
        """
        return len(self._subscribers)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Fan an event out to every currently-connected client.

        Builds the Contract section 34 envelope (`event_type`, `timestamp`,
        `payload`) once and pushes the same dict onto every subscriber's
        queue. A full queue means a slow client -- rather than blocking the
        publisher (which would stall every other client too), the oldest
        queued event for that one client is dropped and the new one takes
        its place.
        """
        event = {
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "payload": payload,
        }
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "SSE client queue full; dropping oldest event for that client"
                )
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except asyncio.QueueEmpty:
                    pass


# Process-wide singleton -- every subscriber and every publisher (the
# periodic fleet_update loop today; per-request battery_update, and later
# alert_created/prediction_update once those features exist) needs to share
# this exact instance for fan-out to work.
broadcaster = Broadcaster()

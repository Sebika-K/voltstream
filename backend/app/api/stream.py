"""Real-time SSE endpoint (Roadmap 3.1, Contract sections 34-36).

`GET /api/v1/stream` is the incremental-update channel the Contract
describes: REST stays the authoritative source of truth (a client fetches a
snapshot via the existing REST endpoints first), and this connection only
carries what changed since then. It deliberately does not replay history --
section 35 is explicit that V1 does not guarantee delivery of every missed
message, and a client that reconnects is expected to refetch a REST snapshot
rather than ask this endpoint to catch it up.

Built on plain `StreamingResponse` rather than a third-party SSE library --
the wire format (`event: <type>\ndata: <json>\n\n`, blank line terminates
each message) is a handful of lines, and the project already has a
principle against collecting infrastructure/dependencies that aren't
solving an actual problem (PRD section 19).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services.broadcaster import broadcaster

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["stream"])

# A comment-only "keep-alive" line (SSE clients ignore lines that don't
# start with a recognized field name), sent whenever no real event has gone
# out for a while. This isn't part of the Contract's event envelope -- it
# exists purely so that intermediary proxies/load balancers don't decide an
# idle connection is dead and silently close it.
_HEARTBEAT_INTERVAL_SECONDS = 15.0


async def _event_stream(request: Request) -> AsyncGenerator[str, None]:
    """Yield SSE-formatted text for one client, from connection to disconnect."""
    queue = broadcaster.subscribe()
    logger.info("SSE client connected (%d total)", broadcaster.subscriber_count)
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=_HEARTBEAT_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield f"event: {event['event_type']}\ndata: {json.dumps(event)}\n\n"
    finally:
        # Always runs, whether the loop broke because the client disconnected
        # or because of an unexpected error -- a dropped connection can never
        # leak a queue that nothing is reading from.
        broadcaster.unsubscribe(queue)
        logger.info("SSE client disconnected (%d remaining)", broadcaster.subscriber_count)


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    """Subscribe to real-time fleet/battery updates (Contract section 34).

    Every connection gets its own subscriber queue (`app/services/
    broadcaster.py`); this endpoint's only job is turning what comes out of
    that queue into properly-formatted SSE text and making sure the queue is
    always cleaned up.
    """
    return StreamingResponse(
        _event_stream(request),
        media_type="text/event-stream",
        headers={
            # Prevents (some) reverse proxies/CDNs from buffering the
            # response, which would defeat the whole point of a live stream.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

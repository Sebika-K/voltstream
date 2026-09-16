"""FastAPI application entrypoint.

Run with:  uvicorn app.main:app --reload   (from the ``backend/`` directory)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.alerts import router as alerts_router
from app.api.batteries import router as batteries_router
from app.api.fleet import router as fleet_router
from app.api.stream import router as stream_router
from app.api.system import router as system_router
from app.api.telemetry import router as telemetry_router
from app.core.config import get_settings
from app.core.errors import APIError, api_error_handler
from app.services.offline_detector import run_offline_detection_loop
from app.services.realtime_publisher import run_fleet_update_publisher

logger = logging.getLogger(__name__)

settings = get_settings()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start/stop the app's background tasks alongside the app itself.

    Two independent loops run for as long as the process is up, neither of
    them tied to any single request:

    - Roadmap 3.1's `fleet_update` publisher (`app/services/
      realtime_publisher.py`), feeding `GET /api/v1/stream`.
    - Roadmap 3.2's offline-detection loop (`app/services/
      offline_detector.py`), which is what actually notices a battery that
      stopped sending telemetry.

    FastAPI's lifespan is the supported place to start/stop background work
    like this (the older `@app.on_event("startup")` style is deprecated).
    Both tasks are created when the app starts serving and cancelled
    cleanly on shutdown, instead of being left to leak.
    """
    tasks = [
        asyncio.create_task(run_fleet_update_publisher()),
        asyncio.create_task(run_offline_detection_loop()),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

# Roadmap 2.5: the frontend (Vite dev server) runs on a different origin
# (http://localhost:5173) than the backend (http://localhost:8000). Browsers
# block cross-origin fetch()/XHR requests by default unless the server opts
# in via CORS headers -- this is a browser security rule, not a backend bug.
# Scoped to the known local dev-server origins only; this will need revisiting
# once the frontend is deployed somewhere with a real origin (Roadmap 5.1).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system_router)
app.include_router(batteries_router)
app.include_router(fleet_router)
app.include_router(telemetry_router)
app.include_router(stream_router)
app.include_router(alerts_router)

# Registered once, here, so every product endpoint that raises APIError gets
# the Contract's {"error": {"code", "message"}} envelope automatically -- see
# app/core/errors.py for why this exists instead of using HTTPException.
app.add_exception_handler(APIError, api_error_handler)

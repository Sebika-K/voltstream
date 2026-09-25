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
from app.core.errors import (
    DATABASE_UNAVAILABLE_ERRORS,
    APIError,
    api_error_handler,
    database_unavailable_handler,
)
from app.core.logging_config import configure_logging
from app.core.request_context import RequestContextMiddleware
from app.services import model_registry
from app.services.metrics_refresher import run_metrics_refresh_loop
from app.services.offline_detector import run_offline_detection_loop
from app.services.realtime_publisher import run_fleet_update_publisher

logger = logging.getLogger(__name__)

settings = get_settings()

# Roadmap 5.2: set up structured logging before anything else can log.
configure_logging(level=settings.LOG_LEVEL, log_format=settings.LOG_FORMAT, service="backend")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start/stop the app's background tasks alongside the app itself, and
    load the ML model artifact exactly once (Roadmap 4.5, Contract section
    46: "The backend loads the model once during application startup ...
    MUST NOT reload the artifact for every request.").

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

    The model load isn't a task -- it's a one-shot step, done before either
    loop starts, so a request that arrives the instant the app is "up" sees
    whatever the load attempt actually produced (a real model, or `None`
    meaning "use the baseline") rather than racing it.
    """
    model_registry.set_loaded_model(model_registry.load_model_artifact(settings.MODEL_ARTIFACT_PATH))

    tasks = [
        asyncio.create_task(run_fleet_update_publisher()),
        asyncio.create_task(run_offline_detection_loop()),
        asyncio.create_task(run_metrics_refresh_loop()),
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
# Scoped to an explicit allow-list (settings.CORS_ORIGINS, configurable per
# environment) rather than "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added after CORSMiddleware, which makes it the outermost layer: every request
# (including CORS preflight checks) gets a request ID and a log line.
app.add_middleware(RequestContextMiddleware)

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

# Database overloaded or unreachable -> a controlled 503 instead of an unhandled 500
# (Contract section 51). See app/core/errors.py for exactly which errors count.
for _db_error in DATABASE_UNAVAILABLE_ERRORS:
    app.add_exception_handler(_db_error, database_unavailable_handler)

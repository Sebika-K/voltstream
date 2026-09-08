"""Liveness and readiness endpoints.

Deliberately outside the ``/api/v1`` prefix used by product APIs: the TDD's API design
(section 11) and the Implementation Contract (section 33) both list ``GET /health`` and
``GET /ready`` as bare, unversioned paths, distinct from the versioned product surface.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.db.session import check_database_connection

router = APIRouter(tags=["system"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict:
    """Report application liveness.

    Must succeed even when PostgreSQL is unavailable -- it answers "is the process
    alive and able to handle requests", not "can it serve database-backed data".
    """
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe")
async def ready() -> JSONResponse:
    """Report whether the application can currently serve database-dependent requests.

    Returns 200 with ``{"status": "ready", "database": "connected"}`` when PostgreSQL is
    reachable, and 503 with ``{"status": "not_ready", "database": "unavailable"}``
    otherwise, per the Implementation Contract.
    """
    if await check_database_connection():
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "database": "connected"},
        )
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "database": "unavailable"},
    )

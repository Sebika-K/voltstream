"""FastAPI application entrypoint.

Run with:  uvicorn app.main:app --reload   (from the ``backend/`` directory)
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.batteries import router as batteries_router
from app.api.system import router as system_router
from app.core.config import get_settings
from app.core.errors import APIError, api_error_handler

settings = get_settings()

app = FastAPI(title=settings.APP_NAME)

app.include_router(system_router)
app.include_router(batteries_router)

# Registered once, here, so every product endpoint that raises APIError gets
# the Contract's {"error": {"code", "message"}} envelope automatically -- see
# app/core/errors.py for why this exists instead of using HTTPException.
app.add_exception_handler(APIError, api_error_handler)

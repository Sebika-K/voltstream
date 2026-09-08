"""FastAPI application entrypoint.

Run with:  uvicorn app.main:app --reload   (from the ``backend/`` directory)
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.system import router as system_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.APP_NAME)

app.include_router(system_router)

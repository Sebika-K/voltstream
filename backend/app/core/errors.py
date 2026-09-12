"""The shared API error envelope (TDD section 12 / Contract's error contract).

Every product endpoint that needs to report a business-logic error (not found,
conflict, etc.) should raise `APIError` rather than FastAPI's own
`HTTPException` -- `HTTPException`'s default body is `{"detail": ...}`, but the
Contract specifies a consistent shape instead:

    {"error": {"code": "...", "message": "..."}}

Registering this once, here, in `app.main`, means every future endpoint (1.7's
ingestion, later battery lookups, etc.) gets the same error shape for free
just by raising `APIError` -- nobody has to remember to hand-build the
envelope themselves.

Pydantic's own validation errors (a malformed request body) are unaffected --
those still return FastAPI's default 422 shape, which the Contract doesn't
override.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class APIError(Exception):
    """A business-logic error that should reach the client as the Contract's
    `{"error": {"code", "message"}}` envelope."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:  # noqa: ARG001
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )

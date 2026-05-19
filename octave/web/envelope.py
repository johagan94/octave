"""Response envelope helpers. Every route returns ``ApiResponse[T]``."""

from __future__ import annotations

from typing import Any, TypeVar

from fastapi.responses import JSONResponse

from .models import ApiError, ApiResponse

T = TypeVar("T")


def ok(data: Any) -> ApiResponse:
    """Wrap successful payload in the standard envelope."""
    return ApiResponse(data=data)


def err(code: str, message: str, status: int = 400, details: dict | None = None) -> JSONResponse:
    """Return a JSONResponse with the standard error envelope.

    Use directly from a route when you need a non-200 status; for raised
    HTTPException, install the global handler in app.py instead.
    """
    body = ApiResponse(error=ApiError(code=code, message=message, details=details))
    return JSONResponse(status_code=status, content=body.model_dump(exclude_none=True))

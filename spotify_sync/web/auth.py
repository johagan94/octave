"""Optional X-API-Key gate. Empty ``API_KEY`` env var = auth disabled.

Apply as a router-level dependency on the protected ``/api`` router.
``/api/health`` is intentionally exempt so the Docker healthcheck works
even if the user has not configured a key (or if they later rotate it).
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("API_KEY", "").strip()
    if not expected:
        return  # auth disabled
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid_api_key")

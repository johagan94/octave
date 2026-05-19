"""Optional X-API-Key gate. Empty ``API_KEY`` env var = auth disabled.

Reads from os.environ first, then falls back to the persistent settings.json
store so that the API key can be configured entirely from the UI.

Apply as a router-level dependency on the protected ``/api`` router.
``/api/health`` is intentionally exempt so the Docker healthcheck works
even if the user has not configured a key (or if they later rotate it).
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException


def _get_api_key() -> str:
    """Return the configured API key.  Env var takes priority, then
    settings.json."""
    env_val = os.environ.get("API_KEY", "").strip()
    if env_val:
        return env_val
    try:
        from .settings import get_setting
        return get_setting("API_KEY")
    except Exception:
        return ""


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = _get_api_key()
    if not expected:
        return  # auth disabled
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid_api_key")

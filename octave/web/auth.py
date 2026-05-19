"""HTTP Basic Auth gate.

Set AUTH_PASSWORD (and optionally AUTH_USERNAME) via env var or the Settings
UI to protect the API. Leaving AUTH_PASSWORD empty disables auth entirely
(LAN-trust mode — suitable when Octave is behind a reverse proxy or VPN).

FastAPI's HTTPBasic triggers the browser's native credential dialog on the
first unauthenticated request. The browser caches the credentials and sends
them with every subsequent request — including EventSource (SSE log tail) —
with no extra JS plumbing required.

Only /api/health is exempt (Docker healthcheck must not need credentials).
"""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

_security = HTTPBasic(auto_error=False)


def _get_credentials() -> tuple[str, str]:
    """Return (username, password). Empty password means auth is disabled."""
    try:
        from .settings import get_setting
        password = get_setting("AUTH_PASSWORD").strip()
        username = get_setting("AUTH_USERNAME").strip() or "octave"
        return username, password
    except Exception:
        return "octave", ""


def require_auth(
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> None:
    """FastAPI dependency — enforces Basic Auth when AUTH_PASSWORD is set."""
    username, password = _get_credentials()
    if not password:
        return  # auth disabled

    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="Octave"'},
        )

    # Use constant-time comparison to prevent timing attacks
    ok_user = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        username.encode("utf-8"),
    )
    ok_pass = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        password.encode("utf-8"),
    )
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="Octave"'},
        )

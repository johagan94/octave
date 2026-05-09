"""Async reachability pings for Spotify / Jellyfin / Lidarr.

Each function returns an ``IntegrationStatus`` filled in with what we
could determine. Failures are *expected* states, not exceptions — the
dashboard renders ``configured: false`` or ``reachable: false`` as a
normal "you have setup work to do" UI state.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import config_path
from .models import IntegrationStatus

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(5.0, connect=3.0)


def _load_raw_config() -> dict[str, Any]:
    """Best-effort config load that does NOT require credentials.

    ``spotify_sync.config.load_config`` calls ``sys.exit(1)`` if env vars
    are missing; the web app must be able to start *before* the user has
    configured anything, so we read the JSON directly here.
    """
    path = config_path()
    if not path.exists():
        return {}
    try:
        import json
        with path.open() as fh:
            return json.load(fh)
    except Exception as exc:
        log.warning("failed to read config %s: %s", path, exc)
        return {}


# ── Spotify ───────────────────────────────────────────────────────────

def check_spotify() -> IntegrationStatus:
    """Spotify is 'reachable' if a valid (or refreshable) token exists.

    We do NOT trigger an OAuth flow here — that's a UI-driven action.
    """
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    csec = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    configured = bool(cid and csec)

    cache_path = Path(os.environ.get("SPOTIFY_TOKEN_CACHE", ".spotify_token_cache"))
    if not configured:
        return IntegrationStatus(configured=False, reachable=False,
                                 error="SPOTIFY_CLIENT_ID/SECRET not set")
    if not cache_path.exists():
        return IntegrationStatus(configured=True, reachable=False,
                                 error="no token cache — first-run OAuth pending")
    try:
        import json
        with cache_path.open() as fh:
            tok = json.load(fh)
    except Exception as exc:
        return IntegrationStatus(configured=True, reachable=False,
                                 error=f"token cache unreadable: {exc}")

    expires_at = tok.get("expires_at", 0)
    has_refresh = bool(tok.get("refresh_token"))
    fresh = time.time() < expires_at - 30
    return IntegrationStatus(
        configured=True,
        reachable=fresh or has_refresh,
        detail={"expires_at": expires_at, "has_refresh_token": has_refresh},
        error=None if (fresh or has_refresh) else "token expired and not refreshable",
    )


# ── Jellyfin ──────────────────────────────────────────────────────────

async def check_jellyfin() -> IntegrationStatus:
    cfg = _load_raw_config().get("jellyfin", {})
    url = os.environ.get("JELLYFIN_URL") or cfg.get("url")
    api_key = os.environ.get("JELLYFIN_API_KEY", "").strip()
    user_id = os.environ.get("JELLYFIN_USER_ID", "").strip()
    configured = bool(url and api_key and user_id)
    if not configured:
        missing = [k for k, v in [("URL", url), ("API key", api_key), ("user id", user_id)] if not v]
        return IntegrationStatus(configured=False, reachable=False,
                                 error=f"missing: {', '.join(missing)}")

    base = url.rstrip("/")
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{base}/System/Info/Public")
        latency = int((time.perf_counter() - started) * 1000)
        if r.status_code != 200:
            return IntegrationStatus(configured=True, reachable=False,
                                     latency_ms=latency,
                                     error=f"HTTP {r.status_code}")
        info = r.json()
        return IntegrationStatus(
            configured=True, reachable=True, latency_ms=latency,
            detail={"version": info.get("Version"),
                    "server_name": info.get("ServerName")},
        )
    except Exception as exc:
        latency = int((time.perf_counter() - started) * 1000)
        return IntegrationStatus(configured=True, reachable=False,
                                 latency_ms=latency, error=str(exc))


# ── Lidarr ────────────────────────────────────────────────────────────

async def check_lidarr() -> IntegrationStatus:
    cfg = _load_raw_config().get("lidarr", {})
    url = os.environ.get("LIDARR_URL") or cfg.get("url")
    api_key = os.environ.get("LIDARR_API_KEY", "").strip()
    configured = bool(url and api_key)
    if not configured:
        missing = [k for k, v in [("URL", url), ("API key", api_key)] if not v]
        return IntegrationStatus(configured=False, reachable=False,
                                 error=f"missing: {', '.join(missing)}")

    base = url.rstrip("/")
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{base}/api/v1/system/status",
                headers={"X-Api-Key": api_key},
            )
        latency = int((time.perf_counter() - started) * 1000)
        if r.status_code != 200:
            return IntegrationStatus(configured=True, reachable=False,
                                     latency_ms=latency,
                                     error=f"HTTP {r.status_code}")
        info = r.json()
        return IntegrationStatus(
            configured=True, reachable=True, latency_ms=latency,
            detail={"version": info.get("version"),
                    "branch": info.get("branch")},
        )
    except Exception as exc:
        latency = int((time.perf_counter() - started) * 1000)
        return IntegrationStatus(configured=True, reachable=False,
                                 latency_ms=latency, error=str(exc))

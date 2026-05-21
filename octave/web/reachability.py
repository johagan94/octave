"""Async reachability pings for Spotify / Jellyfin / Lidarr.

Each function returns an ``IntegrationStatus`` filled in with what we
could determine. Failures are *expected* states, not exceptions -- the
dashboard renders ``configured: false`` or ``reachable: false`` as a
normal "you have setup work to do" UI state.

Credentials are read from os.environ first, then fall back to the
persistent settings.json store.
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


def _describe_exc(exc: Exception) -> str:
    """Always return a non-empty, actionable description.

    httpx connect/timeout errors frequently stringify to '' — surface the
    exception type and a hint so the UI never shows a bare 'no detail'.
    """
    msg = str(exc).strip()
    name = type(exc).__name__
    if msg:
        return f"{name}: {msg}"
    if isinstance(exc, httpx.ConnectTimeout):
        return (f"{name}: connection timed out after 3s "
                f"(host unreachable from the container — wrong URL, "
                f"or not on the same Docker network / use host IP not localhost)")
    if isinstance(exc, httpx.ConnectError):
        return f"{name}: connection refused or DNS resolution failed"
    if isinstance(exc, httpx.ReadTimeout):
        return f"{name}: server accepted the connection but did not respond in time"
    return name


def _cred(key: str) -> str:
    """Return a credential: env var first, then settings.json."""
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from .settings import get_setting
        return get_setting(key)
    except Exception:
        return ""


def _load_raw_config() -> dict[str, Any]:
    """Best-effort config load that does NOT require credentials.

    ``octave.config.load_config`` calls ``sys.exit(1)`` if env vars
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


# -- Spotify

def _has_valid_token(cache_path: Path) -> bool:
    """Return True if the token cache has a fresh or refreshable token."""
    if not cache_path.exists():
        return False
    try:
        import json
        with cache_path.open() as fh:
            tok = json.load(fh)
        expires_at = tok.get("expires_at", 0)
        return time.time() < expires_at - 30 or bool(tok.get("refresh_token"))
    except Exception:
        return False


def check_spotify() -> IntegrationStatus:
    """Spotify is 'reachable' if a valid (or refreshable) token exists.

    Supports both PKCE (no client secret) and legacy Authorization Code flows.
    We do NOT trigger an OAuth flow here -- that's a UI-driven action.
    """
    try:
        from ..spotify_auth import resolve_client_id
        cid = resolve_client_id(_cred("SPOTIFY_CLIENT_ID"))
    except Exception:
        cid = _cred("SPOTIFY_CLIENT_ID")
    csec = _cred("SPOTIFY_CLIENT_SECRET")

    if not cid:
        return IntegrationStatus(configured=False, reachable=False,
                                 error="SPOTIFY_CLIENT_ID not set "
                                       "(and no bundled default)")

    data_dir = Path(os.environ.get("SYNC_DATA_DIR", "/app/data"))
    pkce_cache = data_dir / ".spotify_pkce_token"
    legacy_cache = Path(os.environ.get("SPOTIFY_TOKEN_CACHE", ".spotify_token_cache"))

    if _has_valid_token(pkce_cache):
        return IntegrationStatus(
            configured=True, reachable=True,
            detail={"mode": "pkce", "has_refresh_token": True},
        )

    if _has_valid_token(legacy_cache):
        return IntegrationStatus(
            configured=True, reachable=True,
            detail={"mode": "user_token", "has_refresh_token": True},
        )

    if csec:
        return IntegrationStatus(
            configured=True, reachable=False,
            error="No user token. Open Settings -> Connect Spotify to authorize.",
            detail={"mode": "client_credentials", "note": "public playlists only"},
        )

    return IntegrationStatus(
        configured=True, reachable=False,
        error="No token found. Open Settings → Connect Spotify to authorize.",
    )


# -- Jellyfin

async def check_jellyfin() -> IntegrationStatus:
    cfg = _load_raw_config().get("jellyfin", {})
    url = _cred("JELLYFIN_URL") or cfg.get("url")
    api_key = _cred("JELLYFIN_API_KEY")
    user_id = _cred("JELLYFIN_USER_ID")
    configured = bool(url and api_key and user_id)
    if not configured:
        missing = [k for k, v in [("URL", url), ("API key", api_key), ("user id", user_id)] if not v]
        return IntegrationStatus(configured=False, reachable=False,
                                 error="missing: " + ", ".join(missing))

    base = url.rstrip("/")
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(base + "/System/Info/Public")
        latency = int((time.perf_counter() - started) * 1000)
        if r.status_code != 200:
            return IntegrationStatus(configured=True, reachable=False,
                                     latency_ms=latency,
                                     error="HTTP " + str(r.status_code))
        info = r.json()
        return IntegrationStatus(
            configured=True, reachable=True, latency_ms=latency,
            detail={"version": info.get("Version"),
                    "server_name": info.get("ServerName")},
        )
    except Exception as exc:
        latency = int((time.perf_counter() - started) * 1000)
        return IntegrationStatus(configured=True, reachable=False,
                                 latency_ms=latency, error=_describe_exc(exc))


# -- Lidarr

async def check_lidarr() -> IntegrationStatus:
    cfg = _load_raw_config().get("lidarr", {})
    url = _cred("LIDARR_URL") or cfg.get("url")
    api_key = _cred("LIDARR_API_KEY")
    configured = bool(url and api_key)
    if not configured:
        missing = [k for k, v in [("URL", url), ("API key", api_key)] if not v]
        return IntegrationStatus(configured=False, reachable=False,
                                 error="missing: " + ", ".join(missing))

    base = url.rstrip("/")
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                base + "/api/v1/system/status",
                headers={"X-Api-Key": api_key},
            )
        latency = int((time.perf_counter() - started) * 1000)
        if r.status_code != 200:
            return IntegrationStatus(configured=True, reachable=False,
                                     latency_ms=latency,
                                     error="HTTP " + str(r.status_code))
        info = r.json()
        return IntegrationStatus(
            configured=True, reachable=True, latency_ms=latency,
            detail={"version": info.get("version"),
                    "branch": info.get("branch")},
        )
    except Exception as exc:
        latency = int((time.perf_counter() - started) * 1000)
        return IntegrationStatus(configured=True, reachable=False,
                                 latency_ms=latency, error=_describe_exc(exc))


async def check_listenbrainz() -> IntegrationStatus:
    token = _cred("LISTENBRAINZ_TOKEN")
    if not token:
        return IntegrationStatus(configured=False, reachable=False,
                                 error="LISTENBRAINZ_TOKEN not set (optional)")
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                "https://api.listenbrainz.org/1/status/get-dump-info",
            )
        latency = int((time.perf_counter() - started) * 1000)
        if r.status_code == 200:
            return IntegrationStatus(configured=True, reachable=True,
                                     latency_ms=latency,
                                     detail={"status": "connected"})
        return IntegrationStatus(configured=True, reachable=False,
                                 latency_ms=latency,
                                 error="HTTP " + str(r.status_code))
    except Exception as exc:
        latency = int((time.perf_counter() - started) * 1000)
        return IntegrationStatus(configured=True, reachable=False,
                                 latency_ms=latency, error=_describe_exc(exc))


async def check_lastfm() -> IntegrationStatus:
    api_key = _cred("LASTFM_API_KEY")
    if not api_key:
        return IntegrationStatus(configured=False, reachable=False,
                                 error="LASTFM_API_KEY not set (optional)")
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                "https://ws.audioscrobbler.com/2.0/",
                params={"method": "chart.getTopTracks", "api_key": api_key,
                        "format": "json"},
            )
        latency = int((time.perf_counter() - started) * 1000)
        if r.status_code == 200 and r.json().get("tracks"):
            return IntegrationStatus(configured=True, reachable=True,
                                     latency_ms=latency,
                                     detail={"status": "connected"})
        err = r.json().get("message", "HTTP " + str(r.status_code)) if r.status_code != 200 else "no data"
        return IntegrationStatus(configured=True, reachable=False,
                                 latency_ms=latency, error=str(err))
    except Exception as exc:
        latency = int((time.perf_counter() - started) * 1000)
        return IntegrationStatus(configured=True, reachable=False,
                                 latency_ms=latency, error=_describe_exc(exc))

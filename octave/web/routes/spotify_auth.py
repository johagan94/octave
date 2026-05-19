"""Spotify PKCE OAuth API routes.

GET  /api/spotify/auth-url    -- generate PKCE auth URL, start callback server
GET  /api/spotify/auth-status -- current auth state (token valid/expired/absent)
DELETE /api/spotify/token     -- revoke stored token
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException

from ...spotify_auth import (
    DEFAULT_REDIRECT_URI,
    SCOPES,
    ensure_callback_server,
    generate_auth_url,
    get_status,
    has_bundled_client_id,
    refresh_access_token,
    resolve_client_id,
    revoke_token,
)
from ..envelope import ok
from ..settings import get_setting

log = logging.getLogger(__name__)

router = APIRouter(prefix="/spotify")


@router.get("/auth-url")
def spotify_auth_url():
    """Start a PKCE auth flow. Returns the Spotify authorization URL."""
    client_id = resolve_client_id(get_setting("SPOTIFY_CLIENT_ID"))
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail="No Spotify Client ID available. Set one in Settings, "
                   "or ship a bundled OCTAVE_BUNDLED_SPOTIFY_CLIENT_ID.",
        )
    redirect_uri = get_setting("SPOTIFY_REDIRECT_URI") or DEFAULT_REDIRECT_URI
    port = int(urlparse(redirect_uri).port or 8888)
    if not ensure_callback_server(port):
        raise HTTPException(
            status_code=503,
            detail=f"Could not bind the OAuth callback listener on port {port}. "
                   f"Ensure the port is free and mapped (docker-compose: "
                   f"{port}:{port}).",
        )
    url, state = generate_auth_url(client_id, redirect_uri)
    return ok(data={
        "auth_url": url,
        "state": state,
        "redirect_uri": redirect_uri,
        "scopes": SCOPES.split(),
        "using_bundled_client_id": not get_setting("SPOTIFY_CLIENT_ID")
        and has_bundled_client_id(),
    })


@router.get("/auth-status")
def spotify_auth_status():
    """Return current Spotify PKCE auth status, refreshing token if expired."""
    status = get_status()
    if not status["authenticated"] and status.get("has_refresh_token"):
        token = refresh_access_token()
        if token:
            status = get_status()
    status["client_id_available"] = bool(resolve_client_id(get_setting("SPOTIFY_CLIENT_ID")))
    status["bundled_client_id"] = (
        not get_setting("SPOTIFY_CLIENT_ID") and has_bundled_client_id()
    )
    return ok(data=status)


@router.delete("/token")
def spotify_disconnect():
    """Revoke the stored Spotify PKCE token."""
    revoke_token()
    return ok(data={"disconnected": True})

"""Spotify PKCE OAuth API routes.

GET  /callback                -- OAuth callback (no auth, root-level)
GET  /api/spotify/auth-url    -- generate PKCE auth URL, start callback server
GET  /api/spotify/auth-status -- current auth state (token valid/expired/absent)
DELETE /api/spotify/token     -- revoke stored token
"""

from __future__ import annotations

import html as _html
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...spotify_auth import (
    DEFAULT_REDIRECT_URI,
    SCOPES,
    _CALLBACK_PAGE,
    _exchange_code as _pkce_exchange,
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

# Root-level router — mounted without a prefix so /callback is reachable
# at the app root (same port as the main UI).  No auth middleware: Spotify
# sends the user's browser here directly.
callback_router = APIRouter()


@callback_router.get("/callback", response_class=HTMLResponse, include_in_schema=False)
def spotify_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Spotify PKCE OAuth callback — handles the browser redirect from Spotify."""
    if error:
        safe = _html.escape(error)
        return HTMLResponse(
            _CALLBACK_PAGE.format(
                icon="&#x274C;",
                title="Authorization denied",
                body=f"<p>Spotify returned: <strong>{safe}</strong></p>"
                     "<p>You can close this tab.</p>",
            )
        )
    if not code or not state:
        return HTMLResponse(
            _CALLBACK_PAGE.format(
                icon="&#x26A0;&#xFE0F;",
                title="Invalid callback",
                body="<p>Missing <code>code</code> or <code>state</code> parameter.</p>",
            ),
            status_code=400,
        )
    try:
        _pkce_exchange(code, state)
        log.info("Spotify PKCE auth completed via FastAPI /callback")
        return HTMLResponse(
            _CALLBACK_PAGE.format(
                icon="&#x2705;",
                title="Spotify connected!",
                body="<p>You can close this tab and return to Octave.</p>"
                     "<script>setTimeout(()=>window.close(),2000);</script>",
            )
        )
    except Exception as exc:
        log.error("Spotify PKCE callback exchange failed: %s", exc)
        safe = _html.escape(str(exc))
        return HTMLResponse(
            _CALLBACK_PAGE.format(
                icon="&#x274C;",
                title="Token exchange failed",
                body=f"<p>You can close this tab.</p><pre>{safe}</pre>",
            ),
            status_code=500,
        )


def _redirect_uri_for_request(request: Request) -> str:
    """Return the redirect URI for the PKCE auth flow.

    Priority:
    1. Explicit SPOTIFY_REDIRECT_URI from settings (always honoured)
    2. DEFAULT_REDIRECT_URI for bundled client (no custom app)
    3. Constructed from request origin (proxy-aware: x-forwarded-host/proto)
    4. DEFAULT_REDIRECT_URI as last resort
    """
    configured = get_setting("SPOTIFY_REDIRECT_URI")
    if configured:
        return configured

    custom_client = bool(get_setting("SPOTIFY_CLIENT_ID"))
    if not custom_client:
        return DEFAULT_REDIRECT_URI

    forwarded_host = request.headers.get("x-forwarded-host", "").strip()
    if forwarded_host:
        # Behind a reverse proxy — trust the forwarded headers; the proxy owns
        # TLS termination and port, so we don't append one ourselves.
        forwarded_host = forwarded_host.split(",", 1)[0].strip()
        scheme = (
            request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
            or "https"
        )
        return f"{scheme}://{forwarded_host}/callback"

    # Direct access — derive from the actual request URL
    hostname = request.url.hostname or ""
    if not hostname or hostname in {"localhost", "127.0.0.1", "::1"}:
        return DEFAULT_REDIRECT_URI

    scheme = request.url.scheme or "http"
    port = request.url.port
    _standard = {"http": 80, "https": 443}
    if port and port != _standard.get(scheme):
        return f"{scheme}://{hostname}:{port}/callback"
    return f"{scheme}://{hostname}/callback"


@router.get("/auth-url")
def spotify_auth_url(request: Request):
    """Start a PKCE auth flow. Returns the Spotify authorization URL."""
    client_id = resolve_client_id(get_setting("SPOTIFY_CLIENT_ID"))
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail="No Spotify Client ID available. Set one in Settings, "
                   "or ship a bundled OCTAVE_BUNDLED_SPOTIFY_CLIENT_ID.",
        )
    redirect_uri = _redirect_uri_for_request(request)
    # FastAPI handles /callback directly, so a separate listener is only
    # needed when the redirect URI explicitly targets a non-8000 port (e.g.
    # 8888 for legacy Caddy setups).  Best-effort — don't block auth if it
    # fails, because /callback on port 8000 always works as a fallback.
    _uri_port = urlparse(redirect_uri).port
    _cb_port = _uri_port if _uri_port and _uri_port != 8000 else 8888
    ensure_callback_server(_cb_port)
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

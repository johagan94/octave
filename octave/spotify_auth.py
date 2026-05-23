"""PKCE OAuth lifecycle for Spotify.

Manages the authorization code + PKCE exchange without requiring a client
secret.  The callback is received by a lightweight HTTP server that this
module starts on-demand (port parsed from the configured redirect_uri).

Usage:
    url, state = generate_auth_url(client_id, redirect_uri)
    ensure_callback_server(port)   # starts the listener once
    # ... user visits url, authorizes, server handles /callback ...
    token = get_valid_access_token()   # returns access_token str or None
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

log = logging.getLogger(__name__)

SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"

# Spotify Client ID resolution order:
#   1. SPOTIFY_CLIENT_ID env var
#   2. Settings UI value (settings.json)
#   3. OCTAVE_BUNDLED_SPOTIFY_CLIENT_ID env var, for private builds only
#
# PKCE does not require a client secret, but public installs should normally
# use their own Spotify app because Spotify development-mode apps are
# allowlisted. Maintainers can still ship a public Client ID at runtime with
# OCTAVE_BUNDLED_SPOTIFY_CLIENT_ID for private builds.
BUNDLED_CLIENT_ID = (
    os.environ.get("OCTAVE_BUNDLED_SPOTIFY_CLIENT_ID", "").strip()
)

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"

# Abandoned auth attempts are pruned after this many seconds so the pending
# map cannot grow without bound.
_PENDING_TTL = 600

# state -> {verifier, client_id, redirect_uri, created}
_pending: dict[str, dict] = {}
_pending_lock = threading.Lock()

_token_lock = threading.Lock()

_callback_server: HTTPServer | None = None
_callback_port: int | None = None
_server_lock = threading.Lock()


def resolve_client_id(explicit: str = "") -> str:
    """Return the effective Client ID: explicit value, else the bundled one.

    Returns "" only if neither is set (bundled still a placeholder), so
    callers can surface a clear "not configured" error.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    return BUNDLED_CLIENT_ID.strip()


def has_bundled_client_id() -> bool:
    return bool(BUNDLED_CLIENT_ID.strip())


def _token_path() -> Path:
    return Path(os.environ.get("SPOTIFY_TOKEN_CACHE", "/app/data/.spotify_pkce_token"))


def _save_token(token: dict) -> None:
    """Atomically persist the token with owner-only (0600) permissions.

    The file contains a long-lived refresh_token, so it must not be
    world-readable and a crash mid-write must not corrupt it.
    """
    path = _token_path()
    with _token_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(token, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # best effort (e.g. Windows / odd filesystems)
        tmp.replace(path)
    log.info("Spotify PKCE token saved to %s", path)


def load_token() -> dict | None:
    path = _token_path()
    if not path.exists():
        return None
    try:
        with _token_lock, path.open() as f:
            return json.load(f)
    except Exception as exc:
        log.warning("Failed to read Spotify PKCE token: %s", exc)
        return None


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def _prune_pending_locked() -> None:
    """Drop abandoned auth attempts. Caller must hold _pending_lock."""
    now = time.time()
    stale = [s for s, v in _pending.items() if now - v.get("created", 0) > _PENDING_TTL]
    for s in stale:
        _pending.pop(s, None)
    if stale:
        log.debug("Pruned %d stale PKCE session(s)", len(stale))


def generate_auth_url(client_id: str, redirect_uri: str) -> tuple[str, str]:
    """Register a new PKCE session and return (auth_url, state)."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    with _pending_lock:
        _prune_pending_locked()
        _pending[state] = {
            "verifier": verifier,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "created": time.time(),
        }
    params = {
        "response_type": "code",
        "client_id": client_id,
        "scope": SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    url = "https://accounts.spotify.com/authorize?" + urlencode(params)
    log.info("Generated Spotify PKCE auth URL (client_id prefix: %s...)", client_id[:8])
    return url, state


def _exchange_code(code: str, state: str) -> None:
    """Exchange auth code + PKCE verifier for tokens and persist them."""
    with _pending_lock:
        session = _pending.pop(state, None)
    if not session:
        raise ValueError(f"No PKCE session for state={state!r}")
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": session["redirect_uri"],
            "client_id": session["client_id"],
            "code_verifier": session["verifier"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json()
    token["expires_at"] = int(time.time()) + token.get("expires_in", 3600)
    token["client_id"] = session["client_id"]
    _save_token(token)


def refresh_access_token() -> dict | None:
    """Refresh using the stored refresh_token. Returns new token dict or None."""
    cached = load_token()
    if not cached or "refresh_token" not in cached:
        return None
    client_id = cached.get("client_id", "")
    if not client_id:
        log.warning("Spotify PKCE: no client_id in cached token, cannot refresh")
        return None
    try:
        resp = requests.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": cached["refresh_token"],
                "client_id": client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()
        new_token = resp.json()
        new_token["expires_at"] = int(time.time()) + new_token.get("expires_in", 3600)
        new_token.setdefault("refresh_token", cached["refresh_token"])
        new_token["client_id"] = client_id
        _save_token(new_token)
        log.info("Spotify PKCE token refreshed successfully")
        return new_token
    except Exception as exc:
        log.warning("Spotify PKCE token refresh failed: %s", exc)
        return None


def get_valid_access_token() -> str | None:
    """Return a valid access token, refreshing automatically if needed."""
    cached = load_token()
    if not cached:
        return None
    if time.time() > cached.get("expires_at", 0) - 60:
        refreshed = refresh_access_token()
        return (refreshed or {}).get("access_token")
    return cached.get("access_token")


def get_status() -> dict:
    """Return current auth status suitable for the API response."""
    cached = load_token()
    if not cached:
        return {"authenticated": False, "reason": "no_token"}
    expires_at = cached.get("expires_at", 0)
    if time.time() > expires_at:
        return {
            "authenticated": False,
            "reason": "expired",
            "has_refresh_token": "refresh_token" in cached,
        }
    return {
        "authenticated": True,
        "expires_at": expires_at,
        "scope": cached.get("scope", ""),
    }


def revoke_token() -> None:
    """Delete the stored PKCE token."""
    path = _token_path()
    if path.exists():
        path.unlink()
        log.info("Spotify PKCE token revoked")


_CALLBACK_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Octave – Spotify Auth</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;padding:60px 24px;font-family:ui-monospace,"Cascadia Code",Menlo,monospace;
     background:#060608;color:#e4e4ec;font-size:15px;line-height:1.6;text-align:center}}
.icon{{font-size:52px;margin-bottom:12px}}
h2{{margin:0 0 10px;font-size:22px;font-weight:700}}
p{{margin:0 0 8px;color:#9b9bad}}
pre{{background:#15151d;border:1px solid #222230;border-radius:8px;padding:12px;
     font-size:12px;overflow:auto;color:#f87171;text-align:left;max-width:600px;margin:16px auto 0}}
</style>
</head>
<body>
<div class="icon">{icon}</div>
<h2>{title}</h2>
{body}
</body>
</html>"""


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        log.info("Spotify PKCE callback received: %s (from %s)", self.path, self.client_address[0])
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        error = params.get("error", [None])[0]
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]

        if error:
            log.warning("Spotify OAuth error from callback: %s", error)
            safe = html.escape(error)
            self._respond(200, _CALLBACK_PAGE.format(
                icon="&#x274C;",
                title="Authorization denied",
                body=f"<p>Spotify returned: <strong>{safe}</strong></p><p>You can close this tab.</p>",
            ).encode())
            return

        if not code or not state:
            self._respond(400, _CALLBACK_PAGE.format(
                icon="&#x26A0;&#xFE0F;",
                title="Invalid callback",
                body="<p>Missing <code>code</code> or <code>state</code> parameter.</p>",
            ).encode())
            return

        try:
            _exchange_code(code, state)
            log.info("Spotify PKCE auth completed successfully")
            self._respond(200, _CALLBACK_PAGE.format(
                icon="&#x2705;",
                title="Spotify connected!",
                body="<p>You can close this tab and return to Octave.</p>"
                     "<script>setTimeout(()=>window.close(),2000);</script>",
            ).encode())
        except Exception as exc:
            log.error("Spotify PKCE token exchange failed: %s", exc)
            safe = html.escape(str(exc))
            self._respond(500, _CALLBACK_PAGE.format(
                icon="&#x274C;",
                title="Token exchange failed",
                body=f"<p>You can close this tab.</p><pre>{safe}</pre>",
            ).encode())

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def ensure_callback_server(port: int = 8888) -> bool:
    """Start the PKCE callback HTTP server if not already running.

    Returns True if a server is listening on ``port`` (already or newly),
    False if it could not be bound.
    """
    global _callback_server, _callback_port
    with _server_lock:
        if _callback_server is not None:
            if _callback_port != port:
                log.warning(
                    "Spotify callback server already running on port %s; "
                    "requested port %s will not take effect until restart",
                    _callback_port, port,
                )
            return _callback_port == port
        try:
            server = HTTPServer(("0.0.0.0", port), _CallbackHandler)
            _callback_server = server
            _callback_port = port
            thread = threading.Thread(
                target=server.serve_forever,
                daemon=True,
                name="spotify-pkce-callback",
            )
            thread.start()
            log.info("Spotify PKCE callback server started on port %d", port)
            return True
        except OSError as exc:
            log.warning("Could not start Spotify callback server on port %d: %s", port, exc)
            return False

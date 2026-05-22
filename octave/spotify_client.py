"""Spotify OAuth + playlist/album fetching."""

import logging
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

log = logging.getLogger(__name__)

SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"


def _token_cache_path() -> Path:
    return Path(os.environ.get("SPOTIFY_TOKEN_CACHE", ".spotify_token_cache"))


def _try_pkce_client() -> Optional[spotipy.Spotify]:
    """Return a Spotify client from the persisted PKCE token, or None.

    A user-authorized PKCE token grants full private-playlist access and
    must be preferred over client-credentials (public-only) even when a
    client_secret also happens to be configured.
    """
    from octave.spotify_auth import get_valid_access_token
    token = get_valid_access_token()
    if token:
        log.info("Spotify: using PKCE access token (user-authorized)")
        return spotipy.Spotify(auth=token)
    return None


def _make_pkce_client(client_id: str) -> spotipy.Spotify:
    """Return a Spotify client using the persisted PKCE token, or raise."""
    sp = _try_pkce_client()
    if sp is not None:
        return sp
    raise RuntimeError(
        "Spotify not authorized via PKCE. "
        "Open Octave Settings and click 'Connect Spotify'."
    )


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that captures the ?code= from Spotify's redirect."""

    code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if "code" in params:
            _OAuthCallbackHandler.code = params["code"][0]
            body = b"<h2>Auth successful! You can close this tab.</h2>"
        elif "error" in params:
            _OAuthCallbackHandler.error = params["error"][0]
            body = b"<h2>Auth failed. Check the terminal for details.</h2>"
        else:
            body = b"<h2>Unexpected request.</h2>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):  # suppress default access logs
        pass


def _run_local_server(port: int) -> HTTPServer:
    server = HTTPServer(("0.0.0.0", port), _OAuthCallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    return server


def make_spotify_client(cfg: dict) -> spotipy.Spotify:
    """Return an authenticated Spotify client.

    When client_secret is absent: uses the PKCE token stored by the web UI.
    When client_secret is present: uses Authorization Code flow (legacy) with
    an in-process callback server, falling back to client-credentials for
    public playlists if user auth fails.
    """
    sp_cfg = cfg["spotify"]
    client_secret: str = sp_cfg.get("client_secret", "").strip()

    # A valid user-authorized PKCE token always wins: it grants private
    # playlist access. Only fall back to the legacy/client-credentials
    # path if no PKCE token exists (even when a client_secret is set).
    pkce = _try_pkce_client()
    if pkce is not None:
        return pkce

    if not client_secret:
        return _make_pkce_client(sp_cfg["client_id"])

    redirect_uri: str = sp_cfg.get("redirect_uri", "http://127.0.0.1:8888/callback")
    port = int(urlparse(redirect_uri).port or 8888)

    auth_manager = SpotifyOAuth(
        client_id=sp_cfg["client_id"],
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SPOTIFY_SCOPES,
        cache_path=str(_token_cache_path()),
        open_browser=False,
    )

    token_info = auth_manager.get_cached_token()
    if token_info and not auth_manager.is_token_expired(token_info):
        log.info("Spotify: using cached token (expires in %ds)",
                 token_info["expires_in"])
        return spotipy.Spotify(auth_manager=auth_manager)

    if token_info and auth_manager.is_token_expired(token_info):
        log.info("Spotify: refreshing expired token…")
        try:
            auth_manager.refresh_access_token(token_info["refresh_token"])
            return spotipy.Spotify(auth_manager=auth_manager)
        except Exception as exc:
            log.warning("Spotify: token refresh failed — falling back to client credentials: %s", exc)

    # If we have no token or refresh failed, try client credentials first
    try:
        log.info("Spotify: trying client-credentials flow (public playlist access)…")
        cc_manager = SpotifyClientCredentials(
            client_id=sp_cfg["client_id"],
            client_secret=sp_cfg["client_secret"],
        )
        sp = spotipy.Spotify(auth_manager=cc_manager)
        # Quick test to validate
        sp.track("4iV5W9uYEdYUVa79Axb7Rh")
        log.info("Spotify: client-credentials OK — public playlist access only")
        return sp
    except Exception:
        log.debug("Spotify: client-credentials failed, falling back to OAuth")

    # First-time OAuth
    auth_url = auth_manager.get_authorize_url()
    log.info("=" * 60)
    log.info("SPOTIFY AUTH REQUIRED — first-time setup")
    log.info("=" * 60)
    log.info("Opening browser for Spotify login…")
    log.info("If the browser doesn't open, visit this URL manually:\n\n  %s\n", auth_url)

    _OAuthCallbackHandler.code = None
    _OAuthCallbackHandler.error = None
    _run_local_server(port)

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    log.info("Waiting for Spotify callback on %s …", redirect_uri)
    deadline = time.time() + 300
    while _OAuthCallbackHandler.code is None and _OAuthCallbackHandler.error is None:
        if time.time() > deadline:
            log.error("Timed out waiting for Spotify auth. Re-run to try again.")
            sys.exit(1)
        time.sleep(0.25)

    if _OAuthCallbackHandler.error:
        log.error("Spotify auth error: %s", _OAuthCallbackHandler.error)
        sys.exit(1)

    code = _OAuthCallbackHandler.code
    log.info("Spotify: received auth code, exchanging for token…")
    auth_manager.get_access_token(code, as_dict=False, check_cache=False)
    log.info("Spotify: token saved to %s", _token_cache_path())
    log.info("=" * 60)

    return spotipy.Spotify(auth_manager=auth_manager)


def get_user_playlists(sp: spotipy.Spotify) -> list[dict]:
    """Return all playlists in the authenticated user's library.

    Includes both owned and followed/saved playlists (paginated).
    Spotify-owned editorial/algorithmic playlists (owner id == "spotify")
    are skipped because the Web API no longer exposes their tracks through
    this playlist endpoint.

    Each entry matches the config.json playlist shape so the sync loop
    can consume it directly.
    """
    discovered: list[dict] = []
    skipped_editorial = 0
    result = sp.current_user_playlists(limit=50)
    while result:
        for pl in result.get("items", []):
            if not pl or not pl.get("id"):
                continue
            owner = (pl.get("owner") or {}).get("id", "")
            if owner == "spotify":
                skipped_editorial += 1
                continue
            discovered.append({
                "spotify_playlist_id": pl["id"],
                "jellyfin_playlist_name": pl.get("name") or f"Spotify – {pl['id']}",
                "sync_mode": "add_only",
            })
        result = sp.next(result) if result.get("next") else None
    log.info(
        "Spotify: discovered %d user playlists (skipped %d Spotify-owned editorial)",
        len(discovered), skipped_editorial,
    )
    return discovered


def get_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> list[dict]:
    """Return every track in a Spotify playlist (handles pagination)."""
    tracks: list[dict] = []
    result = sp.playlist_items(
        playlist_id,
        fields=(
            "items(track(id,name,artists(id,name),"
            "album(id,name,album_type,artists(id,name),total_tracks))),next"
        ),
        additional_types=["track"],
    )
    while result:
        for item in result.get("items", []):
            track = item.get("track")
            if track and track.get("id"):
                tracks.append(track)
        result = sp.next(result) if result.get("next") else None
    log.info("  Spotify playlist %s → %d tracks", playlist_id, len(tracks))
    return tracks


def get_album_tracks(sp: spotipy.Spotify, album_id: str) -> list[dict]:
    """Return all track objects for a Spotify album."""
    tracks: list[dict] = []
    result = sp.album_tracks(album_id)
    while result:
        tracks.extend(result["items"])
        result = sp.next(result) if result.get("next") else None
    return tracks


def get_playlist_metadata(sp: spotipy.Spotify, playlist_id: str) -> dict:
    """Return playlist name, cover image URL, and track count."""
    data = sp.playlist(playlist_id, fields="name,images,description,tracks(total)")
    images = sorted(
        data.get("images", []),
        key=lambda i: i.get("width", 0) or 0,
        reverse=True,
    )
    return {
        "name": data.get("name", ""),
        "cover_url": images[0]["url"] if images else None,
        "description": data.get("description", ""),
        "track_count": data.get("tracks", {}).get("total", 0),
    }


def get_playlist_cover(sp: spotipy.Spotify, playlist_id: str) -> Optional[bytes]:
    """Download the largest playlist cover image as raw bytes."""
    meta = get_playlist_metadata(sp, playlist_id)
    url = meta.get("cover_url")
    if not url:
        return None
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.content
    except Exception as exc:
        log.debug("Failed to download cover for %s: %s", playlist_id, exc)
        return None


def primary_artist(track: dict) -> str:
    artists = track.get("artists", [])
    return artists[0]["name"] if artists else ""


def primary_artist_id(track: dict) -> str:
    artists = track.get("artists", [])
    return artists[0]["id"] if artists else ""

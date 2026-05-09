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

import spotipy
from spotipy.oauth2 import SpotifyOAuth

log = logging.getLogger(__name__)

SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative"


def _token_cache_path() -> Path:
    return Path(os.environ.get("SPOTIFY_TOKEN_CACHE", ".spotify_token_cache"))


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
    """Return an authenticated Spotify client (Authorization Code flow).

    First run opens a browser; subsequent runs use the cached token from
    .spotify_token_cache and silently refresh it.
    """
    sp_cfg = cfg["spotify"]
    redirect_uri: str = sp_cfg["redirect_uri"]
    port = int(urlparse(redirect_uri).port or 8888)

    auth_manager = SpotifyOAuth(
        client_id=sp_cfg["client_id"],
        client_secret=sp_cfg["client_secret"],
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
        auth_manager.refresh_access_token(token_info["refresh_token"])
        return spotipy.Spotify(auth_manager=auth_manager)

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


def primary_artist(track: dict) -> str:
    artists = track.get("artists", [])
    return artists[0]["name"] if artists else ""


def primary_artist_id(track: dict) -> str:
    artists = track.get("artists", [])
    return artists[0]["id"] if artists else ""

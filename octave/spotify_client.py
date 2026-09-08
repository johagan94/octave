"""Spotify PKCE client factory and playlist/album fetching.

Public playlist reads fall back to SpotAPI when Spotify's Web API rejects
the developer application with HTTP 403. SpotAPI mirrors the web player's
GraphQL requests and therefore does not require a Premium app owner.
"""

import logging
import os
from pathlib import Path
from typing import Any, Iterable, Optional

import requests
import spotipy

log = logging.getLogger(__name__)


def _token_cache_path() -> Path:
    return Path(os.environ.get("SPOTIFY_TOKEN_CACHE", ".spotify_token_cache"))


class _PKCETokenManager:
    """spotipy auth manager that returns an always-valid PKCE access token.

    spotipy calls ``get_access_token`` before every request, so routing it
    through ``get_valid_access_token`` (which refreshes when the token is within
    60s of expiry) means a long sync can never hit an expired-token 401
    mid-run — the previous code baked a static token into the client at
    creation, which died after ~1 hour.
    """

    def get_access_token(self, as_dict: bool = False):  # noqa: D401 - spotipy hook
        from octave.spotify_auth import get_valid_access_token
        token = get_valid_access_token()
        if not token:
            raise spotipy.SpotifyException(
                401, -1, "Spotify token unavailable; re-connect in Settings"
            )
        return token


def _try_pkce_client() -> Optional[spotipy.Spotify]:
    """Return a Spotify client from the persisted PKCE token, or None."""
    from octave.spotify_auth import get_valid_access_token
    token = get_valid_access_token()
    if token:
        log.info("Spotify: using PKCE access token (user-authorized, auto-refresh)")
        return spotipy.Spotify(auth_manager=_PKCETokenManager())
    return None


class _SpotAPIPublicOnlyClient:
    """Spotipy-shaped trigger for public playlist reads without OAuth."""

    @staticmethod
    def _fallback(*args, **kwargs):
        raise spotipy.SpotifyException(
            403, -1, "No Spotify OAuth token; use the public SpotAPI fallback"
        )

    playlist_items = _fallback
    playlist = _fallback


def make_spotify_client(cfg: dict) -> spotipy.Spotify | _SpotAPIPublicOnlyClient:
    """Return a PKCE client, or a public-only SpotAPI fallback client."""
    sp = _try_pkce_client()
    if sp is not None:
        return sp
    log.warning(
        "Spotify is not authorized via PKCE; public playlists will use SpotAPI"
    )
    return _SpotAPIPublicOnlyClient()


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
    try:
        result = sp.playlist_items(
            playlist_id,
            fields=(
                "items(track(id,name,artists(id,name),"
                "album(id,name,album_type,artists(id,name),total_tracks))),next"
            ),
            additional_types=["track"],
        )
    except spotipy.SpotifyException as exc:
        if exc.http_status != 403:
            raise
        log.warning(
            "Spotify Web API denied playlist %s (403); retrying via SpotAPI",
            playlist_id,
        )
        return _get_spotapi_playlist_tracks(playlist_id)
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
    try:
        data = sp.playlist(playlist_id, fields="name,images,description,tracks(total)")
    except spotipy.SpotifyException as exc:
        if exc.http_status != 403:
            raise
        log.warning(
            "Spotify Web API denied playlist metadata %s (403); retrying via SpotAPI",
            playlist_id,
        )
        return _get_spotapi_playlist_metadata(playlist_id)
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


def get_public_playlist_metadata(playlist_id: str) -> dict:
    """Read public playlist metadata through SpotAPI without OAuth."""
    return _get_spotapi_playlist_metadata(playlist_id)


def _spotapi_playlist(playlist_id: str):
    try:
        from spotapi import PublicPlaylist
    except ImportError as exc:
        raise RuntimeError(
            "SpotAPI fallback is unavailable; install the project requirements"
        ) from exc
    return PublicPlaylist(playlist_id)


def _spotapi_root(payload: dict) -> dict:
    root = (payload.get("data") or {}).get("playlistV2")
    if not isinstance(root, dict):
        raise RuntimeError("SpotAPI returned an invalid playlist response")
    return root


def _spotify_id(uri: Any, kind: str) -> str:
    prefix = f"spotify:{kind}:"
    return uri[len(prefix):] if isinstance(uri, str) and uri.startswith(prefix) else ""


def _spotapi_image_sources(root: dict) -> Iterable[dict]:
    images = root.get("images") or {}
    items = images.get("items", []) if isinstance(images, dict) else images
    for item in items or []:
        sources = item.get("sources", []) if isinstance(item, dict) else []
        yield from sources or []


def _spotapi_track(item: dict) -> Optional[dict]:
    node = item.get("itemV2") or item.get("item") or item
    data = node.get("data", node) if isinstance(node, dict) else {}
    track_id = _spotify_id(data.get("uri"), "track")
    if not track_id:
        return None

    artist_nodes = (data.get("artists") or {}).get("items", [])
    artists = []
    for artist_node in artist_nodes:
        artist = artist_node.get("profile", artist_node)
        artists.append({
            "id": _spotify_id(artist_node.get("uri"), "artist"),
            "name": artist.get("name", ""),
        })

    album_node = data.get("albumOfTrack") or data.get("album") or {}
    album_artists = []
    for artist_node in (album_node.get("artists") or {}).get("items", []):
        profile = artist_node.get("profile", artist_node)
        album_artists.append({
            "id": _spotify_id(artist_node.get("uri"), "artist"),
            "name": profile.get("name", ""),
        })

    return {
        "id": track_id,
        "name": data.get("name", ""),
        "artists": artists,
        "album": {
            "id": _spotify_id(album_node.get("uri"), "album"),
            "name": album_node.get("name", ""),
            "album_type": album_node.get("type", "album"),
            "artists": album_artists or artists,
            "total_tracks": album_node.get("trackCount", 0),
        },
    }


def _get_spotapi_playlist_tracks(playlist_id: str) -> list[dict]:
    playlist = _spotapi_playlist(playlist_id)
    tracks = []
    for content in playlist.paginate_playlist():
        for item in content.get("items", []):
            track = _spotapi_track(item)
            if track:
                tracks.append(track)
    log.info("  SpotAPI playlist %s -> %d tracks", playlist_id, len(tracks))
    return tracks


def _get_spotapi_playlist_metadata(playlist_id: str) -> dict:
    payload = dict(_spotapi_playlist(playlist_id).get_playlist_info(limit=1))
    root = _spotapi_root(payload)
    description = root.get("description", "")
    if isinstance(description, dict):
        description = description.get("text", "")
    images = sorted(
        _spotapi_image_sources(root),
        key=lambda image: image.get("width", 0) or 0,
        reverse=True,
    )
    return {
        "name": root.get("name", ""),
        "cover_url": images[0].get("url") if images else None,
        "description": description,
        "track_count": (root.get("content") or {}).get("totalCount", 0),
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

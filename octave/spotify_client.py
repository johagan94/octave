"""Spotify PKCE client factory and playlist/album fetching."""

import logging
import os
from pathlib import Path
from typing import Optional

import requests
import spotipy

log = logging.getLogger(__name__)


def _token_cache_path() -> Path:
    return Path(os.environ.get("SPOTIFY_TOKEN_CACHE", ".spotify_token_cache"))


def _try_pkce_client() -> Optional[spotipy.Spotify]:
    """Return a Spotify client from the persisted PKCE token, or None."""
    from octave.spotify_auth import get_valid_access_token
    token = get_valid_access_token()
    if token:
        log.info("Spotify: using PKCE access token (user-authorized)")
        return spotipy.Spotify(auth=token)
    return None


def make_spotify_client(cfg: dict) -> spotipy.Spotify:
    """Return an authenticated Spotify client using the stored PKCE token."""
    sp = _try_pkce_client()
    if sp is not None:
        return sp
    raise RuntimeError(
        "Spotify not authorized via PKCE. "
        "Open Octave Settings and click 'Connect Spotify'."
    )


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

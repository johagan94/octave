"""Local playlist JSON import helpers.

Spotify's Web API is still the preferred live source, but account-exported
playlist JSON can act as an offline source when Spotify auth is unavailable.
The helpers here normalize both Octave backups and common Spotify export
shapes into the track dictionaries consumed by the sync pipeline.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable


TRACK_URI_RE = re.compile(r"(?:spotify:track:|open\.spotify\.com/track/)([A-Za-z0-9]+)")
PLAYLIST_URI_RE = re.compile(r"(?:spotify:playlist:|open\.spotify\.com/playlist/)([A-Za-z0-9]+)")
ALBUM_URI_RE = re.compile(r"(?:spotify:album:|open\.spotify\.com/album/)([A-Za-z0-9]+)")
ARTIST_URI_RE = re.compile(r"(?:spotify:artist:|open\.spotify\.com/artist/)([A-Za-z0-9]+)")


def imported_playlists_dir() -> Path:
    return Path(os.environ.get("SYNC_DATA_DIR", "data")) / "imported_playlists"


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join("" if p is None else str(p) for p in parts)
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"local-{prefix}-{digest}"


def _extract_uri_id(value: str | None, regex: re.Pattern[str]) -> str | None:
    if not value:
        return None
    match = regex.search(value)
    return match.group(1) if match else None


def _artist_list(value: Any) -> list[dict]:
    if isinstance(value, list):
        artists = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("name") or item.get("artistName") or item.get("Name")
                artist_id = item.get("id") or _extract_uri_id(item.get("uri"), ARTIST_URI_RE)
            else:
                name = str(item)
                artist_id = None
            if name:
                artists.append({"id": artist_id or "", "name": str(name)})
        return artists
    if value:
        return [{"id": "", "name": str(value)}]
    return []


def _normal_track(
    *,
    title: str,
    artist: str = "",
    album: str = "",
    track_id: str | None = None,
    album_id: str | None = None,
    artist_id: str | None = None,
    duration_ms: int | None = None,
    album_type: str = "album",
    artists: list[dict] | None = None,
) -> dict:
    if artists is None:
        artists = [{"id": artist_id or "", "name": artist}] if artist else []
    if not track_id:
        track_id = _stable_id("track", title, artist, album, duration_ms)
    if not album_id:
        album_id = _stable_id("album", album, artist)
    return {
        "id": track_id,
        "name": title,
        "duration_ms": duration_ms,
        "artists": artists,
        "album": {
            "id": album_id,
            "name": album,
            "album_type": album_type or "album",
            "artists": artists[:1],
        },
        "is_local_json": True,
    }


def _normalize_spotify_track(track: dict) -> dict | None:
    title = track.get("name") or track.get("trackName") or track.get("title")
    if not title:
        return None
    artists = _artist_list(track.get("artists") or track.get("artistName") or track.get("artist"))
    album = track.get("album") if isinstance(track.get("album"), dict) else {}
    album_name = (
        album.get("name")
        or album.get("albumName")
        or track.get("albumName")
        or track.get("album")
        or ""
    )
    track_id = track.get("id") or _extract_uri_id(
        track.get("uri") or track.get("trackUri") or track.get("trackUrl"),
        TRACK_URI_RE,
    )
    artist_id = artists[0].get("id") if artists else ""
    album_id = album.get("id") or _extract_uri_id(album.get("uri") or album.get("albumUri"), ALBUM_URI_RE)
    return _normal_track(
        title=str(title),
        artist=artists[0]["name"] if artists else "",
        album=str(album_name or ""),
        track_id=track_id,
        album_id=album_id,
        artist_id=artist_id,
        duration_ms=track.get("duration_ms") or track.get("durationMs"),
        album_type=album.get("album_type") or album.get("albumType") or "album",
        artists=artists,
    )


def _iter_candidate_tracks(data: dict) -> Iterable[dict]:
    if isinstance(data.get("playlist"), dict):
        for item in data["playlist"].get("tracks") or []:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(data.get("tracks"), list):
        for item in data["tracks"]:
            if isinstance(item, dict):
                yield item.get("track") if isinstance(item.get("track"), dict) else item
        return
    if isinstance(data.get("items"), list):
        for item in data["items"]:
            if not isinstance(item, dict):
                continue
            track = item.get("track")
            yield track if isinstance(track, dict) else item


def normalize_playlist_json(data: dict) -> dict:
    """Return canonical offline playlist JSON.

    Accepted inputs include Octave exports, Spotify Web-API-like playlist JSON,
    and Spotify account-export playlist objects.
    """
    if not isinstance(data, dict):
        raise ValueError("playlist JSON must be an object")

    playlist = data.get("playlist") if isinstance(data.get("playlist"), dict) else data
    name = (
        playlist.get("name")
        or playlist.get("playlistName")
        or playlist.get("title")
        or "Imported Spotify Playlist"
    )
    spotify_id = (
        playlist.get("spotify_id")
        or playlist.get("spotify_playlist_id")
        or playlist.get("id")
        or _extract_uri_id(playlist.get("uri") or playlist.get("playlistUri"), PLAYLIST_URI_RE)
        or _stable_id("playlist", name)
    )

    tracks: list[dict] = []
    for raw in _iter_candidate_tracks(data):
        if "jellyfin_id" in raw:
            track = _normal_track(
                title=str(raw.get("title") or raw.get("name") or ""),
                artist=str(raw.get("artist") or ""),
                album=str(raw.get("album") or ""),
                track_id=raw.get("spotify_id") or raw.get("id"),
                duration_ms=raw.get("duration_ms"),
            )
        else:
            track = _normalize_spotify_track(raw)
        if track and track.get("name"):
            tracks.append(track)

    if not tracks:
        raise ValueError("playlist JSON did not contain any recognizable tracks")

    return {
        "version": 1,
        "source": "local_json",
        "playlist": {
            "name": str(name).strip() or "Imported Spotify Playlist",
            "spotify_id": str(spotify_id),
            "track_count": len(tracks),
            "tracks": tracks,
        },
    }



def normalize_playlist_jsons(data: dict) -> list[dict]:
    """Return one or more canonical offline playlists from an import payload."""
    if isinstance(data, dict) and isinstance(data.get("playlists"), list):
        playlists = [normalize_playlist_json(item) for item in data["playlists"] if isinstance(item, dict)]
        if playlists:
            return playlists
    return [normalize_playlist_json(data)]
def imported_playlist_path(spotify_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", spotify_id).strip("._") or _stable_id("playlist", spotify_id)
    return imported_playlists_dir() / f"{safe}.json"



def save_imported_playlists(data: dict) -> list[tuple[Path, dict]]:
    saved: list[tuple[Path, dict]] = []
    for canonical in normalize_playlist_jsons(data):
        spotify_id = canonical["playlist"]["spotify_id"]
        path = imported_playlist_path(spotify_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(canonical, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        saved.append((path, canonical))
    return saved
def save_imported_playlist(data: dict) -> tuple[Path, dict]:
    saved = save_imported_playlists(data)
    if len(saved) != 1:
        raise ValueError("expected one playlist JSON object; found multiple playlists")
    return saved[0]


def load_imported_playlist_tracks(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_playlist_json(data)["playlist"]["tracks"]


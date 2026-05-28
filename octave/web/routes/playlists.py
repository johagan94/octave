"""Playlist CRUD: GET / POST / DELETE with cover art enrichment.
Also: export/import (JSON), smart playlist generation, and similar-artist discovery.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from ...config import config_path
from ..envelope import err, ok
from ..models import DeleteResult, PlaylistEntry

log = logging.getLogger(__name__)
router = APIRouter()

# Cache cover URLs so we don't re-fetch from Spotify every time
_cover_cache: dict[str, Optional[str]] = {}
_COVER_CACHE_PATH = Path("data/playlist_covers.json")
_DISCOVERED_CACHE_PATH = Path("data/discovered_playlists.json")


def _data_file(name: str) -> Path:
    return Path(os.environ.get("SYNC_DATA_DIR", "data")) / name


def _load_cover_cache() -> None:
    global _cover_cache
    if _cover_cache and not _COVER_CACHE_PATH.exists():
        return
    try:
        _cover_cache = json.loads(_COVER_CACHE_PATH.read_text())
    except Exception:
        _cover_cache = {}


def _save_cover_cache() -> None:
    _COVER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _COVER_CACHE_PATH.write_text(json.dumps(_cover_cache))


def _enrich_playlist(entry: dict) -> PlaylistEntry:
    """Add cover art URL from cache or Spotify API."""
    result = PlaylistEntry(**{k: v for k, v in entry.items()
                              if k in PlaylistEntry.model_fields})
    spotify_id = entry.get("spotify_playlist_id", "")
    if spotify_id in _cover_cache:
        result.cover_url = _cover_cache[spotify_id]
    elif not _cover_cache.get(spotify_id, ...):
        # Lazy-fetch on first view
        try:
            from ...spotify_client import get_playlist_metadata
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth

            sp = None

            # Preferred: PKCE token (no client secret needed)
            from ...spotify_auth import get_valid_access_token
            pkce_token = get_valid_access_token()
            if pkce_token:
                sp = spotipy.Spotify(auth=pkce_token)
            else:
                # Legacy Authorization Code flow (client_secret present)
                client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
                client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
                redirect = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
                token_path = os.environ.get("SPOTIFY_TOKEN_CACHE", ".spotify_token_cache")
                if client_id and client_secret and Path(token_path).exists():
                    auth = SpotifyOAuth(
                        client_id=client_id,
                        client_secret=client_secret,
                        redirect_uri=redirect,
                        scope="playlist-read-private playlist-read-collaborative",
                        cache_path=token_path,
                        open_browser=False,
                    )
                    sp = spotipy.Spotify(auth_manager=auth)

            if sp is not None:
                meta = get_playlist_metadata(sp, spotify_id)
                _cover_cache[spotify_id] = meta.get("cover_url")
                result.cover_url = meta.get("cover_url")
                _save_cover_cache()
            else:
                _cover_cache[spotify_id] = None
        except Exception as exc:
            log.debug("Cover fetch skipped for %s: %s", spotify_id, exc)
            _cover_cache[spotify_id] = None
    return result


def _read_config() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    with path.open() as fh:
        return json.load(fh)


def _write_config(cfg: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        json.dump(cfg, fh, indent=2)
    tmp.replace(path)


def _read_discovered_cache() -> list[dict]:
    path = _data_file("discovered_playlists.json")
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _read_missing_track_playlists() -> list[dict]:
    path = _data_file("missing_tracks.json")
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    playlists: list[dict] = []
    for spotify_id, payload in data.items():
        if not isinstance(payload, dict):
            continue
        playlists.append({
            "spotify_playlist_id": spotify_id,
            "jellyfin_playlist_name": payload.get("playlist_name") or f"Spotify – {spotify_id}",
            "sync_mode": "add_only",
            "configured": False,
        })
    return playlists


def _discover_spotify_playlists() -> list[dict]:
    """Return current Spotify library playlists when PKCE auth is available."""
    try:
        import spotipy

        from ...spotify_auth import get_valid_access_token
        from ...spotify_client import get_user_playlists

        token = get_valid_access_token()
        if not token:
            return []
        playlists = get_user_playlists(spotipy.Spotify(auth=token))
        for playlist in playlists:
            playlist["configured"] = False
        path = _data_file("discovered_playlists.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(playlists, indent=2))
        return playlists
    except Exception as exc:
        log.debug("Spotify playlist discovery skipped: %s", exc)
        return []


def _sync_all_enabled() -> bool:
    try:
        from ..settings import get_setting

        return get_setting("SYNC_ALL_PLAYLISTS", "").strip().lower() in (
            "1", "true", "yes", "on",
        )
    except Exception:
        return False


def _discovered_playlists() -> list[dict]:
    if not _sync_all_enabled():
        return []
    return (
        _discover_spotify_playlists()
        or _read_discovered_cache()
        or _read_missing_track_playlists()
    )


@router.get("/playlists")
def list_playlists():
    _load_cover_cache()
    cfg = _read_config()
    entries = cfg.get("playlists", []) or []
    parsed: list[PlaylistEntry] = []
    seen: set[str] = set()
    for raw in entries:
        try:
            entry = _enrich_playlist({**raw, "configured": True})
            parsed.append(entry)
            seen.add(entry.spotify_playlist_id)
        except Exception:
            if isinstance(raw, dict) and raw.get("spotify_playlist_id"):
                parsed.append(PlaylistEntry(
                    spotify_playlist_id=raw["spotify_playlist_id"],
                    configured=True,
                ))
                seen.add(raw["spotify_playlist_id"])
    for raw in _discovered_playlists():
        spotify_id = raw.get("spotify_playlist_id")
        if not spotify_id or spotify_id in seen:
            continue
        try:
            parsed.append(PlaylistEntry(**{
                k: v for k, v in {**raw, "configured": False}.items()
                if k in PlaylistEntry.model_fields
            }))
            seen.add(spotify_id)
        except Exception:
            log.debug("Skipping invalid discovered playlist row: %r", raw)
    parsed.sort(key=lambda p: (p.jellyfin_playlist_name or p.spotify_playlist_id).lower())
    return ok({"playlists": parsed})


@router.post("/playlists")
def add_playlist(entry: PlaylistEntry = Body(...)):
    cfg = _read_config()
    playlists = cfg.setdefault("playlists", [])
    for existing in playlists:
        if existing.get("spotify_playlist_id") == entry.spotify_playlist_id:
            return err("playlist_exists",
                       f"playlist {entry.spotify_playlist_id} already configured",
                       status=409)
    playlists.append(entry.model_dump(exclude_none=True))
    _write_config(cfg)
    return ok(entry)


@router.delete("/playlists/{spotify_id}")
def delete_playlist(spotify_id: str):
    cfg = _read_config()
    playlists = cfg.get("playlists", []) or []
    new = [p for p in playlists if p.get("spotify_playlist_id") != spotify_id]
    if len(new) == len(playlists):
        return err("playlist_not_found",
                   f"no playlist with id {spotify_id}", status=404)
    cfg["playlists"] = new
    _write_config(cfg)
    return ok(DeleteResult(deleted=True))


# ── Export / Import ───────────────────────────────────────────────────────────

def _jf_client():
    """Instantiate the sync JellyfinClient from the merged config."""
    from ...config import load_config
    from ...jellyfin_client import JellyfinClient
    try:
        return JellyfinClient(load_config())
    except Exception as exc:
        raise HTTPException(503, f"Jellyfin not configured: {exc}")


@router.get("/playlists/export/{spotify_id}")
def export_playlist(spotify_id: str):
    """Download a configured playlist as an Octave JSON backup."""
    cfg = _read_config()
    entry = next(
        (p for p in cfg.get("playlists", []) if p.get("spotify_playlist_id") == spotify_id),
        None,
    )
    if not entry:
        raise HTTPException(404, f"No playlist configured for Spotify ID {spotify_id!r}")

    jf_name = entry.get("jellyfin_playlist_name") or f"Spotify – {spotify_id}"
    jf = _jf_client()
    pl = jf.find_playlist_by_name(jf_name)
    if not pl:
        raise HTTPException(404, f"Jellyfin playlist {jf_name!r} not found — sync it first")

    items = jf.get_playlist_items_rich(pl["Id"])
    tracks = []
    for item in items:
        artist_items = item.get("ArtistItems") or []
        artists = [a.get("Name", "") for a in artist_items if a.get("Name")]
        tracks.append({
            "jellyfin_id": item.get("Id"),
            "title": item.get("Name", ""),
            "artist": artists[0] if artists else item.get("AlbumArtist", ""),
            "album": item.get("Album", ""),
            "year": item.get("ProductionYear"),
            "duration_ms": (item.get("RunTimeTicks") or 0) // 10_000,
        })

    payload = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source": "octave",
        "playlist": {
            "name": jf_name,
            "spotify_id": spotify_id,
            "jellyfin_id": pl["Id"],
            "track_count": len(tracks),
            "tracks": tracks,
        },
    }
    safe_name = "".join(c if c.isalnum() or c in " -_." else "_" for c in jf_name)
    return Response(
        content=json.dumps(payload, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.octave.json"'},
    )


@router.post("/playlists/import")
def import_playlist(body: dict = Body(...)):
    """Recreate a Jellyfin playlist from an Octave JSON backup.

    Tries direct Jellyfin ID lookup first; falls back to fuzzy title+artist
    matching for tracks whose IDs have changed (library rescan, migration).
    """
    if body.get("version") != 1 or "playlist" not in body:
        raise HTTPException(400, "Not a valid Octave export (expected version:1 + playlist key)")

    pl_data = body["playlist"]
    name = (pl_data.get("name") or "").strip()
    tracks = pl_data.get("tracks") or []
    if not name:
        raise HTTPException(400, "playlist.name is required")

    jf = _jf_client()
    jf._build_index()

    matched_ids: list[str] = []
    skipped = 0
    for track in tracks:
        jf_id = track.get("jellyfin_id")
        found = False

        if jf_id:
            try:
                item = jf._get(f"/Users/{jf.user_id}/Items/{jf_id}", Fields="Name")
                if item.get("Name"):
                    matched_ids.append(jf_id)
                    found = True
            except Exception:
                pass

        if not found:
            title = track.get("title", "")
            artist = track.get("artist", "")
            if title and artist:
                result = jf.find_track(title, artist)
                if result:
                    matched_ids.append(result["Id"])
                    found = True

        if not found:
            skipped += 1

    pl_id = jf.get_or_create_playlist(name)
    if matched_ids:
        jf.add_to_playlist(pl_id, matched_ids)

    return ok({
        "playlist_id": pl_id,
        "name": name,
        "matched": len(matched_ids),
        "skipped": skipped,
        "total": len(tracks),
    })


# ── Smart playlist generator ──────────────────────────────────────────────────

_GENERATE_TYPES = frozenset({"genre", "era", "unplayed", "similar", "top_played"})


@router.post("/playlists/generate")
def generate_playlist(body: dict = Body(...)):
    """Generate a new Jellyfin playlist from a smart query.

    body:
      name        str   — playlist name (required)
      type        str   — genre | era | unplayed | similar | top_played
      params      dict  — type-specific options:
        genre:      { genre: "Rock" }
        era:        { from_year: 1970, to_year: 1979 }
        unplayed:   {}
        similar:    { seed_track_id: "<jellyfinId>" }
        top_played: {}
        all types accept: { limit: 25 }
    """
    gen_type = (body.get("type") or "").lower().strip()
    name = (body.get("name") or "").strip()
    params = body.get("params") or {}
    limit = max(1, min(int(params.get("limit", 25)), 200))

    if not name:
        raise HTTPException(400, "name is required")
    if gen_type not in _GENERATE_TYPES:
        raise HTTPException(400, f"type must be one of: {', '.join(sorted(_GENERATE_TYPES))}")

    jf = _jf_client()

    items: list[dict] = []
    if gen_type == "genre":
        genre = (params.get("genre") or "").strip()
        if not genre:
            raise HTTPException(400, "params.genre required for type=genre")
        items = jf.query_audio_items(genre=genre, limit=limit)

    elif gen_type == "era":
        from_year = max(1800, int(params.get("from_year", 1970)))
        to_year = min(datetime.now().year, int(params.get("to_year", from_year + 9)))
        years = list(range(from_year, to_year + 1))
        items = jf.query_audio_items(years=years, limit=limit)

    elif gen_type == "unplayed":
        items = jf.query_audio_items(is_played=False, limit=limit)

    elif gen_type == "similar":
        seed_id = (params.get("seed_track_id") or "").strip()
        if not seed_id:
            raise HTTPException(400, "params.seed_track_id required for type=similar")
        items = jf.query_audio_items(similar_to=seed_id, limit=limit)

    elif gen_type == "top_played":
        items = jf.query_audio_items(sort_by="PlayCount", limit=limit)

    if not items:
        raise HTTPException(404, "No tracks matched the criteria — try different parameters")

    item_ids = [item["Id"] for item in items if item.get("Id")]
    pl_id = jf.get_or_create_playlist(name)
    jf.add_to_playlist(pl_id, item_ids)

    return ok({
        "playlist_id": pl_id,
        "name": name,
        "track_count": len(item_ids),
        "type": gen_type,
    })

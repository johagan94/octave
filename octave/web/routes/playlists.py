"""Playlist CRUD: GET / POST / DELETE with cover art enrichment."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body

from ...config import config_path
from ..envelope import err, ok
from ..models import DeleteResult, PlaylistEntry

log = logging.getLogger(__name__)
router = APIRouter()

# Cache cover URLs so we don't re-fetch from Spotify every time
_cover_cache: dict[str, Optional[str]] = {}
_COVER_CACHE_PATH = Path("data/playlist_covers.json")


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

            sp_cfg = {}
            cfg_path = config_path()
            if cfg_path.exists():
                with cfg_path.open() as fh:
                    sp_cfg = json.load(fh).get("spotify", {})

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


@router.get("/playlists")
def list_playlists():
    _load_cover_cache()
    cfg = _read_config()
    entries = cfg.get("playlists", []) or []
    parsed: list[PlaylistEntry] = []
    for raw in entries:
        try:
            parsed.append(_enrich_playlist(raw))
        except Exception:
            if isinstance(raw, dict) and raw.get("spotify_playlist_id"):
                parsed.append(PlaylistEntry(spotify_playlist_id=raw["spotify_playlist_id"]))
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

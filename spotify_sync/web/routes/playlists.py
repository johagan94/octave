"""Playlist CRUD: GET / POST / DELETE."""

from __future__ import annotations

import json

from fastapi import APIRouter, Body

from ...config import config_path
from ..envelope import err, ok
from ..models import DeleteResult, PlaylistEntry

router = APIRouter()


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
    cfg = _read_config()
    entries = cfg.get("playlists", []) or []
    parsed: list[PlaylistEntry] = []
    for raw in entries:
        try:
            parsed.append(PlaylistEntry(**raw))
        except Exception:
            # Tolerate legacy entries we can't parse — surface their id at least
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

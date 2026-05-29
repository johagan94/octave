"""Sync trigger + status + history + missing tracks endpoints.
Also: Last.fm → Jellyfin historical play-count import.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import threading
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, Body, Path as FPath, Query
from fastapi.responses import Response
from pydantic import BaseModel

from .. import db
from ..envelope import err, ok
from ..runner import get_runner

log = logging.getLogger(__name__)
router = APIRouter()

# ── Last.fm history import state ───────────────────────────────────────────────

_import_lock = threading.Lock()


def _history_state_path() -> Path:
    return Path(os.environ.get("SYNC_DATA_DIR", "data")) / "lastfm_import_state.json"


def _save_history_state(state: dict) -> None:
    p = _history_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(p)


def _run_lastfm_import(from_ts: int | None) -> None:
    """Synchronous worker — runs in a thread-pool executor."""
    from ...config import load_config
    from ...jellyfin_client import JellyfinClient
    from ...lastfm import LastFMClient
    from ..settings import get_setting

    started_at = datetime.now(timezone.utc).isoformat()

    lastfm_user = get_setting("LASTFM_USERNAME")
    lastfm_key = get_setting("LASTFM_API_KEY")

    if not lastfm_user or not lastfm_key:
        _save_history_state({
            "status": "error",
            "error": "LASTFM_USERNAME and LASTFM_API_KEY must be configured in Settings",
            "started_at": started_at,
        })
        _import_lock.release()
        return

    # Inherit watermark from previous run unless caller overrides
    if from_ts is None:
        try:
            prev = json.loads(_history_state_path().read_text())
            from_ts = prev.get("last_imported_ts")
        except Exception:
            pass

    _save_history_state({
        "status": "running",
        "started_at": started_at,
        "current_page": 0,
        "total_pages": "?",
        "imported_count": 0,
        "matched": 0,
        "unmatched": 0,
        "last_imported_ts": from_ts,
    })

    try:
        cfg = load_config()
        jf = JellyfinClient(cfg)
        jf._build_index()
        lfm = LastFMClient(api_key=lastfm_key)

        # First request establishes pagination metadata
        first_params: dict = {
            "method": "user.getRecentTracks",
            "user": lastfm_user,
            "limit": 200,
            "page": 1,
            "extended": 1,
        }
        if from_ts is not None:
            first_params["from"] = int(from_ts)

        first = lfm._get(**first_params)
        attr = first.get("recenttracks", {}).get("@attr", {})
        total_pages = int(attr.get("totalPages") or 1)
        total_tracks = int(attr.get("total") or 0)

        _save_history_state({
            "status": "running",
            "started_at": started_at,
            "current_page": 0,
            "total_pages": total_pages,
            "total_tracks": total_tracks,
            "imported_count": 0,
            "matched": 0,
            "unmatched": 0,
            "last_imported_ts": from_ts,
        })

        imported = matched = 0
        latest_ts: int | None = from_ts

        for page in range(1, total_pages + 1):
            if page == 1:
                page_data = first
            else:
                try:
                    page_params: dict = {
                        "method": "user.getRecentTracks",
                        "user": lastfm_user,
                        "limit": 200,
                        "page": page,
                        "extended": 1,
                    }
                    if from_ts is not None:
                        page_params["from"] = int(from_ts)
                    page_data = lfm._get(**page_params)
                except Exception as exc:
                    log.warning("Last.fm page %d failed: %s", page, exc)
                    continue

            for track in page_data.get("recenttracks", {}).get("track", []):
                if "date" not in track:
                    continue  # skip "now playing" marker

                ts = int(track.get("date", {}).get("uts", 0) or 0)
                title = (track.get("name") or "").strip()
                artist_raw = track.get("artist") or {}
                artist = (
                    artist_raw.get("name", "") if isinstance(artist_raw, dict)
                    else str(artist_raw)
                ).strip()

                if not title or not artist:
                    continue

                jf_item = jf.find_track(title, artist)
                if jf_item:
                    try:
                        date_played = (
                            datetime.utcfromtimestamp(ts).strftime("%Y%m%d%H%M%S")
                            if ts else None
                        )
                        jf.mark_played(jf_item["Id"], date_played=date_played)
                        matched += 1
                    except Exception as exc:
                        log.debug("mark_played failed %s: %s", jf_item["Id"], exc)

                imported += 1
                if ts and (latest_ts is None or ts > latest_ts):
                    latest_ts = ts

            _save_history_state({
                "status": "running",
                "started_at": started_at,
                "current_page": page,
                "total_pages": total_pages,
                "total_tracks": total_tracks,
                "imported_count": imported,
                "matched": matched,
                "unmatched": imported - matched,
                "last_imported_ts": latest_ts,
            })

        _save_history_state({
            "status": "done",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "current_page": total_pages,
            "total_pages": total_pages,
            "total_tracks": total_tracks,
            "imported_count": imported,
            "matched": matched,
            "unmatched": imported - matched,
            "last_imported_ts": latest_ts,
        })
        log.info("Last.fm history import done: %d scrobbles, %d matched", imported, matched)

    except Exception as exc:
        log.exception("Last.fm history import failed")
        _save_history_state({
            "status": "error",
            "error": str(exc),
            "started_at": started_at,
        })
    finally:
        _import_lock.release()

SyncTypeParam = Literal["playlists", "all"]


class SyncRequest(BaseModel):
    playlist_ids: Optional[List[str]] = None


@router.post("/sync/lastfm_history")
async def start_lastfm_history(body: dict = Body(default={})):
    """Start a background Last.fm → Jellyfin play-count import.

    Fetches all scrobbles since the last import watermark (or ``from_ts``
    if provided) and calls Jellyfin's PlayedItems API for each matched track.
    Returns 409 if an import is already running.
    """
    if not _import_lock.acquire(blocking=False):
        return err("already_running", "A Last.fm history import is already running", status=409)
    from_ts = body.get("from_ts") or None
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_lastfm_import, from_ts)
    return ok({"message": "Import started"})



@router.post("/sync/{type}")
async def trigger_sync(
    type: SyncTypeParam = FPath(..., description="Sync target: 'playlists' or 'all'"),
    body: SyncRequest = Body(default=SyncRequest()),
):
    """Start a sync. Pass ``playlist_ids`` to sync a subset; omit for all.
    Returns 409 if one is already running."""
    runner = get_runner()
    run = await runner.trigger(type, playlist_ids=body.playlist_ids)
    return ok(run)


@router.get("/sync/status")
def get_sync_status():
    """Current run if active, else last completed run, else idle."""
    runner = get_runner()
    return ok(runner.status())


@router.get("/sync/history")
def get_sync_history(limit: int = Query(default=10, le=50)):
    """List recent sync runs."""
    rows = db.get_run_history(limit)
    return ok({
        "runs": [
            {
                "id": r["id"],
                "type": r["type"],
                "status": r["status"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "matched": r["matched"] or 0,
                "missing": r["missing"] or 0,
                "albums_requested": r["albums_requested"] or 0,
                "waiting_lidarr": r["waiting_lidarr"] or 0,
                "error": r["error"],
            }
            for r in rows
        ],
    })


@router.get("/sync/history/{run_id}")
def get_sync_run_detail(run_id: int):
    """Get per-playlist details for a specific sync run."""
    rows = db.get_sync_items(run_id)
    return ok({
        "items": [
            {
                "spotify_id": r["spotify_id"],
                "playlist_name": r["playlist_name"],
                "status": r["status"],
                "matched": r["matched"] or 0,
                "missing": r["missing"] or 0,
                "albums_requested": r["albums_requested"] or 0,
                "waiting_lidarr": r["waiting_lidarr"] or 0,
                "error": r["error"],
            }
            for r in rows
        ],
    })



@router.get("/sync/lastfm_history/status")
def get_lastfm_history_status():
    """Return the state of the current or most recent Last.fm history import."""
    try:
        data = json.loads(_history_state_path().read_text())
        return ok(data)
    except FileNotFoundError:
        return ok({"status": "idle"})
    except Exception:
        return ok({"status": "idle"})


@router.get("/sync/missing")
def get_missing_tracks():
    """Return all missing tracks from the most recent sync."""
    path = Path("data/missing_tracks.json")
    if not path.exists():
        return ok({"playlists": {}})
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return ok({"playlists": {}})
    return ok({"playlists": data})


@router.get("/sync/missing/download/{spotify_id}")
def download_missing_csv(spotify_id: str):
    """Download missing tracks for a playlist as CSV."""
    path = Path("data/missing_tracks.json")
    if not path.exists():
        return Response(content="No data", media_type="text/plain")
    data = json.loads(path.read_text())
    pl = data.get(spotify_id, {})
    tracks = pl.get("tracks", [])
    if not tracks:
        return Response(content="No tracks", media_type="text/plain")
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["spotify_id", "title", "artist", "album", "album_type", "spotify_url"])
    for track in tracks:
        writer.writerow([
            _csv_cell(track.get("spotify_id", "")),
            _csv_cell(track.get("title", "")),
            _csv_cell(track.get("artist", "")),
            _csv_cell(track.get("album", "")),
            _csv_cell(track.get("album_type", "")),
            _csv_cell(track.get("spotify_url", "")),
        ])
    return Response(
        content=out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=missing_{spotify_id}.csv"},
    )


def _csv_cell(value: object) -> str:
    text = str(value or "")
    if text[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text

"""Sync trigger + status + history + missing tracks endpoints."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, Body, Path as FPath, Query
from fastapi.responses import Response
from pydantic import BaseModel

from .. import db
from ..envelope import ok
from ..runner import get_runner

router = APIRouter()

SyncTypeParam = Literal["playlists", "all"]


class SyncRequest(BaseModel):
    playlist_ids: Optional[List[str]] = None


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
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["spotify_id", "title", "artist", "album", "album_type", "spotify_url"])
    for t in tracks:
        writer.writerow([
            t.get("spotify_id", ""),
            t.get("title", ""),
            t.get("artist", ""),
            t.get("album", ""),
            t.get("album_type", ""),
            t.get("spotify_url", ""),
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=missing_{spotify_id}.csv"},
    )

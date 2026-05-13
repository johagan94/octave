"""Sync trigger + status + history endpoints."""

from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, Body, Path, Query
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
    type: SyncTypeParam = Path(..., description="Sync target: 'playlists' or 'all'"),
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

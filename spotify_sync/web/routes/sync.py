"""Sync trigger + status endpoints."""

from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, Body, Path
from pydantic import BaseModel

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

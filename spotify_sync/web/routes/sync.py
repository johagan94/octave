"""Sync trigger + status endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Path

from ..envelope import ok
from ..runner import get_runner

router = APIRouter()

SyncTypeParam = Literal["playlists", "all"]


@router.post("/sync/{type}")
async def trigger_sync(
    type: SyncTypeParam = Path(..., description="Sync target: 'playlists' or 'all'"),
):
    """Start a sync. Returns 409 if one is already running."""
    runner = get_runner()
    run = await runner.trigger(type)
    return ok(run)


@router.get("/sync/status")
def get_sync_status():
    """Current run if active, else last completed run, else idle."""
    runner = get_runner()
    return ok(runner.status())

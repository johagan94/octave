"""GET /api/setup/status — dashboard's first-load endpoint.

Returns ``configured`` + ``reachable`` for each integration. Designed so
"user has not finished setup" is a normal response shape, not an error.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from ..envelope import ok
from ..models import SetupStatus
from ..reachability import check_jellyfin, check_lidarr, check_spotify
from ..reachability import check_listenbrainz, check_lastfm
from ..reachability import _load_raw_config

router = APIRouter()


@router.get("/setup/status")
async def get_setup_status():
    spotify = check_spotify()  # sync; reads token cache
    jellyfin, lidarr, listenbrainz, lastfm = await asyncio.gather(
        check_jellyfin(), check_lidarr(),
        check_listenbrainz(), check_lastfm(),
    )

    cfg = _load_raw_config()
    playlists = cfg.get("playlists", []) if isinstance(cfg, dict) else []

    return ok(SetupStatus(
        spotify=spotify,
        jellyfin=jellyfin,
        lidarr=lidarr,
        listenbrainz=listenbrainz,
        lastfm=lastfm,
        config_loaded=bool(cfg),
        playlist_count=len(playlists),
    ))

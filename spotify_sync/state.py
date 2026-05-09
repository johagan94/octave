"""Sync state persistence. State tracks Lidarr album request status across
runs so multi-step workflows (artist add → refresh → album monitor) survive
restarts."""

import json
import os
from pathlib import Path


def state_path() -> Path:
    return Path(os.environ.get("SYNC_STATE", "sync_state.json"))


def load_state() -> dict:
    path = state_path()
    if path.exists():
        with path.open() as fh:
            return json.load(fh)
    return {"lidarr_requested_albums": {}, "jellyfin_playlists": {}}


def save_state(state: dict) -> None:
    path = state_path()
    with path.open("w") as fh:
        json.dump(state, fh, indent=2)

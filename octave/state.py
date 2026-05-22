"""Sync state persistence. State tracks Lidarr album request status across
runs so multi-step workflows (artist add → refresh → album monitor) survive
restarts. Also tracks tracks waiting for Lidarr to download."""

import json
import os
import threading
from pathlib import Path
from typing import Set

# Lock for thread-safe state mutations during parallel Lidarr requests
_state_lock = threading.Lock()


def state_path() -> Path:
    return Path(os.environ.get("SYNC_STATE", "sync_state.json"))


def load_state() -> dict:
    path = state_path()
    if path.exists():
        with path.open() as fh:
            return json.load(fh)
    return {
        "lidarr_requested_albums": {},
        "waiting_for_lidarr_tracks": {},
        "jellyfin_playlists": {},
    }


def save_state(state: dict) -> None:
    path = state_path()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with _state_lock:
        with open(tmp_path, "w") as fh:
            json.dump(state, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp_path), str(path))


def get_waiting_track_ids(state: dict) -> Set[str]:
    """Return Spotify track IDs currently waiting for Lidarr download."""
    return set(state.get("waiting_for_lidarr_tracks", {}).keys())

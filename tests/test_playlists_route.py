import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from octave.web.routes import playlists


def test_list_playlists_merges_auto_discovered_when_sync_all_enabled(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "playlists": [{
            "spotify_playlist_id": "configured-id",
            "jellyfin_playlist_name": "Configured",
            "sync_mode": "full_sync",
        }],
    }))

    env = {
        "SYNC_CONFIG": str(config_path),
        "SYNC_DATA_DIR": str(tmp_path),
    }
    with patch.dict(os.environ, env, clear=False), \
            patch.object(playlists, "_sync_all_enabled", return_value=True), \
            patch.object(playlists, "_discover_spotify_playlists", return_value=[
                {
                    "spotify_playlist_id": "auto-id",
                    "jellyfin_playlist_name": "Auto",
                    "sync_mode": "add_only",
                },
                {
                    "spotify_playlist_id": "configured-id",
                    "jellyfin_playlist_name": "Duplicate",
                    "sync_mode": "add_only",
                },
            ]):
        result = playlists.list_playlists()

    rows = result.data["playlists"]
    by_id = {row.spotify_playlist_id: row for row in rows}
    assert set(by_id) == {"configured-id", "auto-id"}
    assert by_id["configured-id"].configured is True
    assert by_id["configured-id"].sync_mode == "full_sync"
    assert by_id["auto-id"].configured is False


def test_discovered_playlists_falls_back_to_missing_tracks(tmp_path):
    missing = {
        "missing-id": {
            "playlist_name": "From Missing Tracks",
            "tracks": [],
        },
    }
    (tmp_path / "missing_tracks.json").write_text(json.dumps(missing))

    with patch.dict(os.environ, {"SYNC_DATA_DIR": str(tmp_path)}, clear=False), \
            patch.object(playlists, "_sync_all_enabled", return_value=True), \
            patch.object(playlists, "_discover_spotify_playlists", return_value=[]):
        rows = playlists._discovered_playlists()

    assert rows == [{
        "spotify_playlist_id": "missing-id",
        "jellyfin_playlist_name": "From Missing Tracks",
        "sync_mode": "add_only",
        "configured": False,
    }]

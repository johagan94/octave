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


def test_import_playlist_registers_local_json_source(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"playlists": []}))

    class FakeJellyfin:
        user_id = "user-1"

        def _build_index(self):
            pass

        def get_library_item(self, item_id):
            return None

        def find_track(self, title, artist, spotify_id=None):
            return {"Id": f"jf-{title}-{artist}"}

        def get_or_create_playlist(self, name):
            return "playlist-jf-id"

        def get_playlist_items(self, playlist_id):
            return []

        def add_to_playlist(self, playlist_id, item_ids):
            self.added = item_ids

    env = {
        "SYNC_CONFIG": str(config_path),
        "SYNC_DATA_DIR": str(tmp_path),
    }
    body = {
        "playlistName": "Offline Mix",
        "playlistUri": "spotify:playlist:playlist123",
        "items": [{
            "track": {
                "trackName": "Song A",
                "artistName": "Artist A",
                "albumName": "Album A",
                "trackUri": "spotify:track:track123",
            },
        }],
    }

    with patch.dict(os.environ, env, clear=False), \
            patch.object(playlists, "_jf_client", return_value=FakeJellyfin()):
        result = playlists.import_playlist(body)

    cfg = json.loads(config_path.read_text())
    assert result.data["name"] == "Offline Mix"
    assert result.data["spotify_id"] == "playlist123"
    assert cfg["playlists"] == [{
        "spotify_playlist_id": "playlist123",
        "jellyfin_playlist_name": "Offline Mix",
        "sync_mode": "add_only",
        "source": "local_json",
        "source_path": str(tmp_path / "imported_playlists" / "playlist123.json"),
    }]

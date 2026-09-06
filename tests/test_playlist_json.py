import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from octave.playlist_json import load_imported_playlist_tracks, normalize_playlist_json, save_imported_playlist


def test_normalizes_spotify_account_export_playlist():
    data = {
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

    result = normalize_playlist_json(data)

    assert result["playlist"]["name"] == "Offline Mix"
    assert result["playlist"]["spotify_id"] == "playlist123"
    track = result["playlist"]["tracks"][0]
    assert track["id"] == "track123"
    assert track["name"] == "Song A"
    assert track["artists"][0]["name"] == "Artist A"
    assert track["album"]["name"] == "Album A"


def test_save_and_load_imported_playlist_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNC_DATA_DIR", str(tmp_path))
    data = {
        "version": 1,
        "playlist": {
            "name": "Backup Mix",
            "spotify_id": "backup123",
            "tracks": [{
                "title": "Song B",
                "artist": "Artist B",
                "album": "Album B",
                "duration_ms": 1000,
            }],
        },
    }

    path, canonical = save_imported_playlist(data)
    tracks = load_imported_playlist_tracks(path)

    assert path == tmp_path / "imported_playlists" / "backup123.json"
    assert json.loads(path.read_text()) == canonical
    assert tracks[0]["name"] == "Song B"
    assert tracks[0]["artists"][0]["name"] == "Artist B"


def test_normalizes_multiple_spotify_export_playlists():
    data = {
        "playlists": [
            {
                "playlistName": "One",
                "items": [{"track": {"trackName": "A", "artistName": "Artist", "albumName": "Album"}}],
            },
            {
                "playlistName": "Two",
                "items": [{"track": {"trackName": "B", "artistName": "Artist", "albumName": "Album"}}],
            },
        ],
    }

    from octave.playlist_json import normalize_playlist_jsons

    results = normalize_playlist_jsons(data)

    assert [p["playlist"]["name"] for p in results] == ["One", "Two"]

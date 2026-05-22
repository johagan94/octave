import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from octave import sync as sync_mod
from octave.sync import sync_playlist


def _track(track_id="spotify-track", name="Song", artist="Artist", album_id="album-id"):
    return {
        "id": track_id,
        "name": name,
        "artists": [{"name": artist, "id": "artist-id"}],
        "album": {
            "id": album_id,
            "name": "Album",
            "album_type": "album",
            "artists": [{"name": artist, "id": "artist-id"}],
        },
    }


class FakeJellyfin:
    def __init__(self, found=None, fail_update=False):
        self._library_cache = [{"Id": "jf-track"}]
        self.found = found
        self.fail_update = fail_update
        self.added = []

    def _build_index(self):
        return None

    def find_track(self, title, artist, spotify_id=None):
        return self.found

    def get_playlists(self):
        return [{"Id": "playlist-id", "Name": "Target"}]

    def get_or_create_playlist(self, name):
        if self.fail_update:
            raise RuntimeError("playlist write denied")
        return "playlist-id"

    def get_playlist_items(self, playlist_id):
        return []

    def add_to_playlist(self, playlist_id, item_ids):
        self.added.extend(item_ids)

    def set_playlist_image(self, playlist_id, image_bytes):
        return True


def test_sync_playlist_raises_when_spotify_fetch_fails():
    with patch.object(sync_mod, "get_playlist_tracks", side_effect=RuntimeError("spotify down")):
        with pytest.raises(RuntimeError, match="Spotify failed to fetch playlist"):
            sync_playlist(
                {"spotify_playlist_id": "playlist-id", "jellyfin_playlist_name": "Target"},
                sp=object(),
                jf=FakeJellyfin(),
                lidarr=None,
                mb=None,
                state={"lidarr_requested_albums": {}},
                playlist_num=1,
                playlist_total=1,
            )


def test_sync_playlist_raises_when_jellyfin_update_fails():
    with patch.object(sync_mod, "get_playlist_tracks", return_value=[_track()]):
        with pytest.raises(RuntimeError, match="Jellyfin failed to update playlist"):
            sync_playlist(
                {"spotify_playlist_id": "playlist-id", "jellyfin_playlist_name": "Target"},
                sp=object(),
                jf=FakeJellyfin(found={"Id": "jf-track"}, fail_update=True),
                lidarr=None,
                mb=None,
                state={"lidarr_requested_albums": {}},
                playlist_num=1,
                playlist_total=1,
            )


def test_sync_playlist_records_missing_without_lidarr(tmp_path):
    with patch.dict(os.environ, {"SYNC_DATA_DIR": str(tmp_path)}, clear=False), \
            patch.object(sync_mod, "get_playlist_tracks", return_value=[_track()]), \
            patch.object(sync_mod, "get_playlist_cover", return_value=None):
        stats = sync_playlist(
            {"spotify_playlist_id": "playlist-id", "jellyfin_playlist_name": "Target"},
            sp=object(),
            jf=FakeJellyfin(found=None),
            lidarr=None,
            mb=None,
            state={"lidarr_requested_albums": {}},
            playlist_num=1,
            playlist_total=1,
        )

    assert stats == {
        "matched": 0,
        "missing": 1,
        "albums_requested": 0,
        "waiting_lidarr": 0,
    }
    assert (tmp_path / "missing_tracks.json").exists()

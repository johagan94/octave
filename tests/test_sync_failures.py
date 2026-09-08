import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from octave import sync as sync_mod
from octave.sync import save_missing_tracks, sync_playlist, write_missing_tracks


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
    def __init__(self, found=None, fail_update=False, playlists=None):
        self._library_cache = [{"Id": "jf-track"}]
        self.found = found
        self.fail_update = fail_update
        self.added = []
        self.deleted = []
        self.created = []
        self.playlists = playlists if playlists is not None else [{"Id": "playlist-id", "Name": "Target"}]

    def _build_index(self):
        return None

    def find_track(self, title, artist, spotify_id=None):
        return self.found

    def get_playlists(self):
        return self.playlists

    def get_or_create_playlist(self, name):
        if self.fail_update:
            raise RuntimeError("playlist write denied")
        self.created.append(name)
        return "playlist-id"

    def get_playlist_items(self, playlist_id):
        return []

    def add_to_playlist(self, playlist_id, item_ids):
        self.added.extend(item_ids)

    def set_playlist_image(self, playlist_id, image_bytes):
        return True

    def delete_playlist(self, playlist_id):
        self.deleted.append(playlist_id)


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
                jf=FakeJellyfin(found={"Id": "jf-track"}, fail_update=True, playlists=[]),
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


def test_sync_playlist_uses_scoped_lidarr_fallback_when_album_catalogue_fails(tmp_path):
    class FailingLidarr:
        album_catalog_unavailable = False

        def get_albums(self):
            self.album_catalog_unavailable = True
            raise RuntimeError("catalogue HTTP 500")

        def get_artists(self):
            return []

    with patch.dict(os.environ, {"SYNC_DATA_DIR": str(tmp_path)}, clear=False), \
            patch.object(sync_mod, "get_playlist_tracks", return_value=[_track()]), \
            patch.object(sync_mod, "get_playlist_cover", return_value=None), \
            patch.object(sync_mod, "request_album_in_lidarr") as request_album:
        stats = sync_playlist(
            {"spotify_playlist_id": "playlist-id", "jellyfin_playlist_name": "Target"},
            sp=object(),
            jf=FakeJellyfin(found=None),
            lidarr=FailingLidarr(),
            mb=object(),
            state={"lidarr_requested_albums": {}, "current_run": "run"},
            playlist_num=1,
            playlist_total=1,
        )

    assert request_album.call_count == 1
    assert stats["albums_requested"] == 1
    assert stats["missing"] == 1


def test_scoped_lidarr_fallback_does_not_search_monitored_album():
    class ScopedLidarr:
        album_catalog_unavailable = True
        _run_artist_lock_guard = threading.Lock()
        _run_artist_locks = {}
        _run_artist_cache = {}

        def find_album_in_library(self, *args):
            raise AssertionError("global album lookup should be skipped")

        def find_artist_in_library(self, name):
            return {"id": 42, "artistName": name}

        def get_artist_albums(self, artist_id):
            assert artist_id == 42
            return [{"id": 99, "title": "Album", "monitored": True}]

        def find_album_in_artist(self, artist_id, album_name, albums):
            return albums[0]

        def monitor_and_search_album(self, album_id):
            raise AssertionError("monitored album should not be searched again")

    state = {"lidarr_requested_albums": {}, "current_run": "run"}
    with patch.object(sync_mod, "save_state"):
        sync_mod.request_album_in_lidarr(
            lidarr=ScopedLidarr(),
            mb=None,
            spotify_album_id="album-id",
            spotify_album_name="Album",
            spotify_artist_id="artist-id",
            spotify_artist_name="Artist",
            state=state,
        )

    assert state["lidarr_requested_albums"]["album-id"] == {
        "status": "already_monitored",
        "lidarr_id": 99,
    }


def test_missing_tracks_store_is_written_once_at_end(tmp_path):
    store = {}
    write_missing_tracks("One", "one", [_track()], str(tmp_path), store=store)
    write_missing_tracks("Two", "two", [_track(track_id="two")], str(tmp_path), store=store)

    assert not (tmp_path / "missing_tracks.json").exists()
    save_missing_tracks(store, str(tmp_path))

    payload = __import__("json").loads((tmp_path / "missing_tracks.json").read_text())
    assert set(payload) == {"one", "two"}


def test_sync_playlist_reuses_state_playlist_mapping():
    jf = FakeJellyfin(
        found={"Id": "jf-track"},
        playlists=[
            {"Id": "mapped-playlist-id", "Name": "Thank You Based God"},
            {"Id": "same-name-duplicate", "Name": "Thank You Based God"},
        ],
    )
    state = {
        "lidarr_requested_albums": {},
        "waiting_for_lidarr_tracks": {},
        "jellyfin_playlists": {"playlist-id": "mapped-playlist-id"},
    }

    with patch.object(sync_mod, "get_playlist_tracks", return_value=[_track()]), \
            patch.object(sync_mod, "get_playlist_cover", return_value=None):
        sync_playlist(
            {
                "spotify_playlist_id": "playlist-id",
                "jellyfin_playlist_name": "Thank You Based God",
            },
            sp=object(),
            jf=jf,
            lidarr=None,
            mb=None,
            state=state,
            playlist_num=1,
            playlist_total=1,
        )

    assert jf.created == []
    assert jf.added == ["jf-track"]
    assert state["jellyfin_playlists"]["playlist-id"] == "mapped-playlist-id"


def test_sync_playlist_rebuild_removes_all_same_name_duplicates():
    jf = FakeJellyfin(
        found={"Id": "jf-track"},
        playlists=[
            {"Id": "old-1", "Name": "thank you based god"},
            {"Id": "old-2", "Name": "Thank You Based God"},
            {"Id": "other", "Name": "Different"},
        ],
    )
    state = {
        "lidarr_requested_albums": {},
        "waiting_for_lidarr_tracks": {},
        "jellyfin_playlists": {"playlist-id": "old-1"},
    }

    with patch.object(sync_mod, "get_playlist_tracks", return_value=[_track()]), \
            patch.object(sync_mod, "get_playlist_cover", return_value=None):
        sync_playlist(
            {
                "spotify_playlist_id": "playlist-id",
                "jellyfin_playlist_name": "Thank You Based God",
                "sync_mode": "rebuild",
            },
            sp=object(),
            jf=jf,
            lidarr=None,
            mb=None,
            state=state,
            playlist_num=1,
            playlist_total=1,
        )

    assert jf.deleted == ["old-1", "old-2"]
    assert jf.created == ["Thank You Based God"]
    assert state["jellyfin_playlists"]["playlist-id"] == "playlist-id"

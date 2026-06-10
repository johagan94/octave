import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from octave import sync as sync_mod
from octave.sync import _MAX_WAIT_RUNS, sync_playlist


def _track(track_id="t1"):
    return {
        "id": track_id,
        "name": "Song",
        "artists": [{"name": "Artist", "id": "artist-id"}],
        "album": {"id": "al", "name": "Album", "album_type": "album",
                  "artists": [{"name": "Artist", "id": "artist-id"}]},
    }


class FakeJellyfin:
    def __init__(self, found):
        self._library_cache = [{"Id": "jf1"}]
        self.found = found

    def _build_index(self, force_reload=False):
        return None

    def find_track(self, title, artist, spotify_id=None):
        return self.found

    def get_playlists(self):
        return [{"Id": "pl", "Name": "Target"}]

    def get_playlist_items(self, playlist_id):
        return []

    def add_to_playlist(self, playlist_id, item_ids):
        pass

    def set_playlist_image(self, playlist_id, image_bytes):
        return True


def _state(runs_waited):
    return {
        "lidarr_requested_albums": {},
        "waiting_for_lidarr_tracks": {
            "t1": {"album_id": "al", "status": "requested", "run": "old", "runs_waited": runs_waited}
        },
        "jellyfin_playlists": {"sp1": "pl"},
    }


def _run(jf, state, tmp_path):
    with patch.dict(os.environ, {"SYNC_DATA_DIR": str(tmp_path)}, clear=False), \
            patch.object(sync_mod, "get_playlist_tracks", return_value=[_track()]), \
            patch.object(sync_mod, "get_playlist_cover", return_value=None):
        return sync_playlist(
            {"spotify_playlist_id": "sp1", "jellyfin_playlist_name": "Target"},
            sp=object(), jf=jf, lidarr=None, mb=None, state=state,
            playlist_num=1, playlist_total=1,
        )


def test_waiting_track_that_landed_is_matched_and_cleared(tmp_path):
    """A track Lidarr has downloaded (now in Jellyfin) must be added to the
    playlist and removed from the wait list — previously it was skipped."""
    state = _state(runs_waited=2)
    stats = _run(FakeJellyfin(found={"Id": "jf1"}), state, tmp_path)

    assert stats["matched"] == 1
    assert "t1" not in state["waiting_for_lidarr_tracks"]


def test_waiting_track_still_missing_stays_waiting(tmp_path):
    state = _state(runs_waited=2)
    stats = _run(FakeJellyfin(found=None), state, tmp_path)

    assert stats["waiting_lidarr"] == 1
    assert stats["missing"] == 0  # not re-treated as missing yet
    assert state["waiting_for_lidarr_tracks"]["t1"]["runs_waited"] == 3


def test_waiting_track_escapes_after_max_runs(tmp_path):
    """After _MAX_WAIT_RUNS with no match, give up waiting and re-treat the
    track as missing so it gets re-requested instead of being stuck forever."""
    state = _state(runs_waited=_MAX_WAIT_RUNS - 1)
    stats = _run(FakeJellyfin(found=None), state, tmp_path)

    assert stats["missing"] == 1
    assert "t1" not in state["waiting_for_lidarr_tracks"]

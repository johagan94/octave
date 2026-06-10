import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from octave import sync as sync_mod
from octave.sync import _normalise_playlist_name, _playlist_name_matches, sync_playlist


def _track(track_id="t1"):
    return {
        "id": track_id,
        "name": "Song",
        "artists": [{"name": "Artist", "id": "artist-id"}],
        "album": {"id": "al", "name": "Album", "album_type": "album",
                  "artists": [{"name": "Artist", "id": "artist-id"}]},
    }


class FakeJellyfin:
    def __init__(self, found, playlists):
        self._library_cache = [{"Id": "jf1"}]
        self.found = found
        self.playlists = playlists
        self.created = []
        self.added = []

    def _build_index(self, force_reload=False):
        return None

    def find_track(self, title, artist, spotify_id=None):
        return self.found

    def get_playlists(self):
        return self.playlists

    def get_or_create_playlist(self, name):
        self.created.append(name)
        return "new-id"

    def get_playlist_items(self, playlist_id):
        return []

    def add_to_playlist(self, playlist_id, item_ids):
        self.added.extend(item_ids)

    def set_playlist_image(self, playlist_id, image_bytes):
        return True


def test_normalise_handles_trailing_space_and_case():
    assert _normalise_playlist_name("Thank You Based God ") == "thank you based god"
    assert _normalise_playlist_name("thank you based god") == "thank you based god"
    assert _playlist_name_matches({"Name": "Thank You Based God"}, "Thank You Based God ")
    assert _playlist_name_matches({"Name": "Best of  90s"}, "Best of 90s")  # collapsed spaces


def test_trailing_space_config_reuses_existing_playlist(tmp_path):
    """The "Thank You Based God" bug: config name has a trailing space, Jellyfin
    stored it trimmed, the mapping is empty — the name lookup must still match
    the existing playlist instead of creating a duplicate every run."""
    jf = FakeJellyfin(
        found={"Id": "jf1"},
        playlists=[{"Id": "existing", "Name": "Thank You Based God"}],  # trimmed by Jellyfin
    )
    state = {"lidarr_requested_albums": {}, "waiting_for_lidarr_tracks": {}, "jellyfin_playlists": {}}

    with patch.dict(os.environ, {"SYNC_DATA_DIR": str(tmp_path)}, clear=False), \
            patch.object(sync_mod, "get_playlist_tracks", return_value=[_track()]), \
            patch.object(sync_mod, "get_playlist_cover", return_value=None):
        sync_playlist(
            {"spotify_playlist_id": "sp1", "jellyfin_playlist_name": "Thank You Based God "},
            sp=object(), jf=jf, lidarr=None, mb=None, state=state,
            playlist_num=1, playlist_total=1,
        )

    assert jf.created == []                                   # no duplicate created
    assert state["jellyfin_playlists"]["sp1"] == "existing"   # mapped to the existing one
    assert jf.added == ["jf1"]

import pytest
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from octave.state import load_state, save_state, state_path


@pytest.fixture
def tmp_state_file(tmp_path):
    state_file = tmp_path / "test_sync_state.json"
    with patch.dict(os.environ, {"SYNC_STATE": str(state_file)}):
        yield state_file


class TestLoadState:
    def test_load_state_returns_defaults_when_file_missing(self, tmp_path):
        with patch.dict(os.environ, {"SYNC_STATE": str(tmp_path / "nonexistent.json")}):
            state = load_state()
            assert state == {
                "lidarr_requested_albums": {},
                "waiting_for_lidarr_tracks": {},
                "jellyfin_playlists": {},
            }

    def test_load_state_reads_existing_file(self, tmp_state_file):
        initial = {"lidarr_requested_albums": {"1": "pending"}, "waiting_for_lidarr_tracks": {}, "jellyfin_playlists": {}}
        tmp_state_file.write_text(json.dumps(initial))
        state = load_state()
        assert state["lidarr_requested_albums"] == {"1": "pending"}


class TestSaveState:
    def test_save_state_writes_valid_json(self, tmp_state_file):
        state = {
            "lidarr_requested_albums": {"1": "pending", "2": "done"},
            "waiting_for_lidarr_tracks": {"10": "waiting"},
            "jellyfin_playlists": {"pl1": "synced"},
        }
        save_state(state)
        assert tmp_state_file.exists()
        loaded = json.loads(tmp_state_file.read_text())
        assert loaded == state

    def test_save_state_is_atomic_no_truncated_file_on_crash(self, tmp_state_file):
        initial = {
            "lidarr_requested_albums": {"1": "initial"},
            "waiting_for_lidarr_tracks": {},
            "jellyfin_playlists": {},
        }
        tmp_state_file.write_text(json.dumps(initial, indent=2))
        new_state = {
            "lidarr_requested_albums": {"1": "updated", "2": "new"},
            "waiting_for_lidarr_tracks": {"10": "waiting"},
            "jellyfin_playlists": {"pl1": "synced"},
        }
        save_state(new_state)
        content = tmp_state_file.read_text()
        loaded = json.loads(content)
        assert loaded == new_state
        tmp_files = list(tmp_state_file.parent.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Leftover temp files: {tmp_files}"

    def test_save_state_overwrites_completely(self, tmp_state_file):
        initial = {
            "lidarr_requested_albums": {"1": "old"},
            "waiting_for_lidarr_tracks": {"99": "stale"},
            "jellyfin_playlists": {},
            "some_old_key": "should_not_persist",
        }
        tmp_state_file.write_text(json.dumps(initial, indent=2))
        new_state = {
            "lidarr_requested_albums": {"2": "new"},
            "waiting_for_lidarr_tracks": {},
            "jellyfin_playlists": {},
        }
        save_state(new_state)
        loaded = json.loads(tmp_state_file.read_text())
        assert "some_old_key" not in loaded
        assert "99" not in loaded.get("waiting_for_lidarr_tracks", {})
        assert loaded == new_state

    def test_save_state_uses_os_replace(self, tmp_state_file):
        """save_state should use os.replace for atomic file replacement."""
        state = {"lidarr_requested_albums": {}, "waiting_for_lidarr_tracks": {}, "jellyfin_playlists": {}}
        
        with patch('os.replace') as mock_replace:
            save_state(state)
            assert mock_replace.called, "save_state must use os.replace for atomic writes"
            assert mock_replace.call_count == 1

    def test_save_state_writes_to_temp_file_first(self, tmp_state_file):
        """save_state should write to a .tmp file before renaming to target."""
        state = {"lidarr_requested_albums": {"1": "test"}, "waiting_for_lidarr_tracks": {}, "jellyfin_playlists": {}}
        
        seen_paths = []
        original_open = open
        
        def tracking_open(*args, **kwargs):
            if args and 'w' in str(args[1] if len(args) > 1 else kwargs.get('mode', '')):
                seen_paths.append(args[0] if args else kwargs.get('file'))
            return original_open(*args, **kwargs)
        
        with patch('builtins.open', tracking_open):
            save_state(state)
        
        # Should have written to a .tmp file, not directly to the target
        assert any(str(p).endswith('.tmp') for p in seen_paths), f"Expected .tmp file in writes, got: {seen_paths}"

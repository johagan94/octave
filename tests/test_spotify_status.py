import json
import os
from unittest.mock import patch

from octave.config import load_config
from octave.web import reachability
from octave.web.routes import spotify_auth


def test_load_config_allows_spotapi_without_oauth_client_id(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "jellyfin": {"url": "http://jellyfin"},
        "playlists": [],
    }))
    env = {
        "SYNC_CONFIG": str(config_path),
        "SYNC_DATA_DIR": str(tmp_path),
        "JELLYFIN_API_KEY": "key",
        "JELLYFIN_USER_ID": "user",
    }
    with patch.dict(os.environ, env, clear=True), \
            patch("octave.spotify_auth.resolve_client_id", return_value=""):
        cfg = load_config()

    assert cfg["spotify"]["client_id"] == ""


def test_reachability_reports_public_spotapi_without_oauth(tmp_path):
    with patch.dict(os.environ, {"SYNC_DATA_DIR": str(tmp_path)}, clear=True), \
            patch.object(reachability, "_cred", return_value=""), \
            patch("octave.spotify_auth.resolve_client_id", return_value=""):
        status = reachability.check_spotify()

    assert status.configured is True
    assert status.reachable is True
    assert status.detail["mode"] == "spotapi"
    assert status.detail["public_only"] is True
    assert status.detail["oauth_client_id_available"] is False


def test_auth_status_advertises_spotapi_fallback():
    base_status = {
        "authenticated": False,
        "has_refresh_token": False,
        "reason": "no token",
    }
    with patch.object(spotify_auth, "get_status", return_value=base_status.copy()), \
            patch.object(spotify_auth, "get_setting", return_value=""), \
            patch.object(spotify_auth, "resolve_client_id", return_value=""), \
            patch.object(spotify_auth, "has_bundled_client_id", return_value=False):
        response = spotify_auth.spotify_auth_status()

    assert response.data["public_fallback_available"] is True
    assert response.data["public_playlist_mode"] == "spotapi"
    assert response.data["client_id_available"] is False

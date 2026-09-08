import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import octave.spotify_auth as spotify_auth
from octave import spotify_client


def test_client_uses_autorefreshing_auth_manager():
    """The client must pull a fresh token per request (auth_manager), not bake a
    static one — otherwise a >1h sync 401s when the access token expires."""
    with patch.object(spotify_auth, "get_valid_access_token", return_value="tok-123"):
        sp = spotify_client.make_spotify_client({})
        assert sp.auth_manager is not None, "client must use an auth_manager, not a static token"
        # spotipy calls this before every request; it should return a live token.
        assert sp.auth_manager.get_access_token() == "tok-123"


def test_auth_manager_reflects_refreshed_token():
    """A later call returns the newly-refreshed token (proves no caching of a
    stale value)."""
    mgr = spotify_client._PKCETokenManager()
    with patch.object(spotify_auth, "get_valid_access_token", side_effect=["first", "second"]):
        assert mgr.get_access_token() == "first"
        assert mgr.get_access_token() == "second"


def test_make_client_uses_public_fallback_when_not_authorized():
    with patch.object(spotify_auth, "get_valid_access_token", return_value=None):
        client = spotify_client.make_spotify_client({})
    assert isinstance(client, spotify_client._SpotAPIPublicOnlyClient)


def test_auth_manager_raises_401_when_token_unavailable():
    """If refresh fails mid-sync, surface a 401 SpotifyException so the runner's
    retry/clear-message path can handle it."""
    import spotipy
    mgr = spotify_client._PKCETokenManager()
    with patch.object(spotify_auth, "get_valid_access_token", return_value=None):
        with pytest.raises(spotipy.SpotifyException):
            mgr.get_access_token()

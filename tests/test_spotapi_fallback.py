from unittest.mock import MagicMock, patch

import pytest
import spotipy

from octave.spotify_client import (
    get_playlist_metadata,
    get_playlist_tracks,
    make_spotify_client,
)


def _forbidden():
    return spotipy.SpotifyException(403, -1, "Premium app owner required")


def _spotapi_payload():
    return {
        "data": {
            "playlistV2": {
                "name": "Fallback Mix",
                "description": {"text": "From the web player"},
                "images": {"items": [{"sources": [
                    {"url": "small.jpg", "width": 64},
                    {"url": "large.jpg", "width": 640},
                ]}]},
                "content": {
                    "totalCount": 1,
                    "items": [{
                        "itemV2": {"data": {
                            "uri": "spotify:track:track1",
                            "name": "A Song",
                            "artists": {"items": [{
                                "uri": "spotify:artist:artist1",
                                "profile": {"name": "An Artist"},
                            }]},
                            "albumOfTrack": {
                                "uri": "spotify:album:album1",
                                "name": "An Album",
                            },
                        }}
                    }],
                },
            }
        }
    }


@patch("octave.spotify_client._spotapi_playlist")
def test_playlist_tracks_fall_back_to_spotapi_on_403(make_playlist):
    sp = MagicMock()
    sp.playlist_items.side_effect = _forbidden()
    make_playlist.return_value.paginate_playlist.return_value = [
        _spotapi_payload()["data"]["playlistV2"]["content"]
    ]

    tracks = get_playlist_tracks(sp, "playlist1")

    assert tracks == [{
        "id": "track1",
        "name": "A Song",
        "artists": [{"id": "artist1", "name": "An Artist"}],
        "album": {
            "id": "album1",
            "name": "An Album",
            "album_type": "album",
            "artists": [{"id": "artist1", "name": "An Artist"}],
            "total_tracks": 0,
        },
    }]


@patch("octave.spotify_client._spotapi_playlist")
def test_playlist_metadata_falls_back_to_spotapi_on_403(make_playlist):
    sp = MagicMock()
    sp.playlist.side_effect = _forbidden()
    make_playlist.return_value.get_playlist_info.return_value = _spotapi_payload()

    metadata = get_playlist_metadata(sp, "playlist1")

    assert metadata == {
        "name": "Fallback Mix",
        "cover_url": "large.jpg",
        "description": "From the web player",
        "track_count": 1,
    }


@patch("octave.spotify_client._spotapi_playlist")
def test_non_403_spotify_errors_are_not_hidden(make_playlist):
    sp = MagicMock()
    sp.playlist_items.side_effect = spotipy.SpotifyException(401, -1, "expired")

    with pytest.raises(spotipy.SpotifyException) as exc_info:
        get_playlist_tracks(sp, "playlist1")

    assert exc_info.value.http_status == 401
    make_playlist.assert_not_called()


@patch("octave.spotify_client._get_spotapi_playlist_tracks")
@patch("octave.spotify_client._try_pkce_client", return_value=None)
def test_public_playlists_work_without_pkce(_pkce_client, spotapi_tracks):
    spotapi_tracks.return_value = [{"id": "track1"}]

    client = make_spotify_client({})

    assert get_playlist_tracks(client, "playlist1") == [{"id": "track1"}]

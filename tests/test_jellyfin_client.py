from unittest.mock import Mock

from octave.jellyfin_client import JellyfinClient
from octave.track_cache import TrackCache


def _client(tmp_path=None):
    cache = TrackCache(tmp_path / "tracks.json") if tmp_path else None
    client = JellyfinClient({
        "jellyfin": {"url": "http://jellyfin", "api_key": "key", "user_id": "user"},
        "match_threshold": 80,
    }, track_cache=cache)
    client._set_library_items([
        {"Id": "1", "Name": "The Sound of Silence", "Artists": ["Simon & Garfunkel"]},
        {"Id": "2", "Name": "Another Song", "Artists": ["Another Artist"]},
    ])
    return client, cache


def test_fuzzy_index_preserves_track_matching():
    client, _cache = _client()

    result = client.find_track("Sound of Silence", "Simon and Garfunkel")

    assert result["Id"] == "1"


def test_cached_track_id_uses_direct_item_index(tmp_path):
    client, cache = _client(tmp_path)
    cache.set("spotify-1", "2")

    assert client.find_track("wrong", "wrong", "spotify-1")["Id"] == "2"
    assert client.get_cache_stats()["hits"] == 1


def test_initially_empty_cache_is_populated(tmp_path):
    client, cache = _client(tmp_path)

    assert client.find_track("Another Song", "Another Artist", "spotify-2")["Id"] == "2"
    assert cache.get("spotify-2") == "2"

    assert client.find_track("Missing Song", "Missing Artist", "missing-id") is None
    assert cache.is_not_found("missing-id")


def test_repeated_missing_track_id_skips_second_fuzzy_scan():
    client, _cache = _client()
    original_titles = client._normalised_titles

    assert client.find_track("Missing Song", "Missing Artist", "missing-id") is None
    client._normalised_titles = None
    assert client.find_track("Missing Song", "Missing Artist", "missing-id") is None
    client._normalised_titles = original_titles


def test_playlist_listing_is_cached_and_create_updates_cache():
    client, _cache = _client()
    client._get = Mock(return_value={"Items": [{"Id": "existing", "Name": "Existing"}]})
    response = Mock()
    response.json.return_value = {"Id": "created"}
    client._post = Mock(return_value=response)

    assert client.get_or_create_playlist("Existing") == "existing"
    assert client.get_or_create_playlist("New") == "created"
    assert client.get_or_create_playlist("New") == "created"
    assert client._get.call_count == 1
    assert client._post.call_count == 1

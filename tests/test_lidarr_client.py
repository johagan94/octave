import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from octave.lidarr_client import LidarrClient


def _client():
    return LidarrClient({"lidarr": {"url": "http://lidarr", "api_key": "k"}})


def test_refresh_artist_deduped_per_run():
    """RefreshArtist is queued at most once per artist per run — the fix for
    Lidarr being hammered with hundreds of refreshes (one per missing album)."""
    c = _client()
    calls = []
    with patch.object(c, "_post", side_effect=lambda path, payload: calls.append(payload) or {}):
        c.refresh_artist(5)
        c.refresh_artist(5)  # duplicate — must be dropped
        c.refresh_artist(5)  # duplicate — must be dropped
        c.refresh_artist(7)
    assert len(calls) == 2
    assert {p["artistId"] for p in calls} == {5, 7}
    assert all(p["name"] == "RefreshArtist" for p in calls)


def test_get_artist_albums_cached_per_run():
    """An artist's album list is fetched once per run, not once per missing
    album by that artist."""
    c = _client()
    calls = []

    def fake_get(path, **params):
        calls.append((path, params))
        return [{"id": 1, "title": "A"}]

    with patch.object(c, "_get", side_effect=fake_get):
        first = c.get_artist_albums(10)
        second = c.get_artist_albums(10)

    assert first == second == [{"id": 1, "title": "A"}]
    assert len(calls) == 1


def test_find_album_by_mbid_exact():
    c = _client()
    albums = [
        {"id": 1, "foreignAlbumId": "rg-1"},
        {"id": 2, "foreignAlbumId": "rg-2"},
    ]
    assert c.find_album_by_mbid("rg-2", albums)["id"] == 2
    assert c.find_album_by_mbid("rg-missing", albums) is None
    assert c.find_album_by_mbid("", albums) is None


def test_get_uses_short_retry_policy():
    c = _client()
    response = type("Response", (), {
        "raise_for_status": lambda self: None,
        "json": lambda self: [],
    })()

    with patch("octave.lidarr_client.http_get_with_retry", return_value=response) as request:
        c._get("/album")

    assert request.call_args.kwargs["max_attempts"] == 2
    assert request.call_args.kwargs["backoff_base"] == 1.0


def test_album_catalogue_failure_is_cached_for_the_run():
    c = _client()
    with patch.object(c, "_get", side_effect=RuntimeError("HTTP 500")) as request:
        for _ in range(2):
            try:
                c.get_albums()
            except RuntimeError:
                pass

    assert request.call_count == 1
    assert c.album_catalog_unavailable is True


def test_artist_album_failure_is_cached_for_the_run():
    c = _client()
    with patch.object(c, "_get", side_effect=RuntimeError("HTTP 500")) as request:
        for _ in range(2):
            try:
                c.get_artist_albums(42)
            except RuntimeError:
                pass

    assert request.call_count == 1


def test_album_needs_search_uses_lidarr_file_statistics():
    c = _client()

    assert c.album_needs_search({
        "statistics": {"trackCount": 10, "trackFileCount": 0},
    }) is True
    assert c.album_needs_search({
        "statistics": {"trackCount": 10, "trackFileCount": 10},
    }) is False
    assert c.album_needs_search({}) is False


def test_album_search_is_deduped_per_run():
    c = _client()
    album = {"id": 99, "monitored": True}

    with patch.object(c, "_get", return_value=album), \
            patch.object(c, "_put") as put, \
            patch.object(c, "_post", return_value={}) as post:
        assert c.monitor_and_search_album(99) is True
        assert c.monitor_and_search_album(99) is False

    assert put.call_count == 1
    assert post.call_count == 1

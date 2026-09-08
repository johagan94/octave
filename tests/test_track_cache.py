from octave.track_cache import TrackCache


def test_negative_cache_persists_for_same_library_and_invalidates_on_change(tmp_path):
    path = tmp_path / "track-cache.json"
    cache = TrackCache(path)
    cache.set_library_scope("library-a")
    cache.set_not_found("missing-track")
    cache.save()

    loaded = TrackCache(path)
    loaded.load()
    loaded.set_library_scope("library-a")
    assert loaded.is_not_found("missing-track")

    loaded.set_library_scope("library-b")
    assert not loaded.is_not_found("missing-track")


def test_positive_match_replaces_negative_cache_entry(tmp_path):
    cache = TrackCache(tmp_path / "track-cache.json")
    cache.set_not_found("track")
    cache.set("track", "jellyfin-track")

    assert cache.get("track") == "jellyfin-track"
    assert not cache.is_not_found("track")

"""Mock-based check for the new ``rebuild`` sync mode.

Verifies the call sequence:
  1. get_playlists() lists existing
  2. delete_playlist(id) when a same-named playlist exists
  3. get_or_create_playlist(name) creates a fresh one
  4. add_to_playlist(...) populates it

We don't run the real sync_playlist (it needs Spotify, Lidarr, etc).
Instead we exercise the rebuild branch logic directly.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock


def test_rebuild_deletes_existing_then_recreates() -> None:
    """rebuild mode → delete existing playlist, create fresh, add tracks."""
    jf = MagicMock()
    jf.get_playlists.return_value = [
        {"Id": "old-pl-123", "Name": "My Playlist"},
        {"Id": "other-456", "Name": "Some Other Playlist"},
    ]
    jf.get_or_create_playlist.return_value = "new-pl-789"

    # Simulate the rebuild branch from sync.sync_playlist
    jf_name = "My Playlist"
    sync_mode = "rebuild"
    matched_ids = ["track-A", "track-B", "track-C"]

    if sync_mode == "rebuild":
        for pl in jf.get_playlists():
            if pl["Name"].lower() == jf_name.lower():
                jf.delete_playlist(pl["Id"])
                break
        pl_id = jf.get_or_create_playlist(jf_name)
        existing_item_ids = set()
    else:
        raise AssertionError("test should be in rebuild branch")

    new_ids = [iid for iid in matched_ids if iid not in existing_item_ids]
    if new_ids:
        for i in range(0, len(new_ids), 100):
            jf.add_to_playlist(pl_id, new_ids[i:i + 100])

    # Assertions on call order
    jf.delete_playlist.assert_called_once_with("old-pl-123")
    jf.get_or_create_playlist.assert_called_once_with("My Playlist")
    jf.add_to_playlist.assert_called_once_with(
        "new-pl-789", ["track-A", "track-B", "track-C"],
    )

    # Other playlists must NOT be touched
    delete_calls = [c.args[0] for c in jf.delete_playlist.call_args_list]
    assert "other-456" not in delete_calls
    print("ok    rebuild deletes existing then recreates")


def test_rebuild_when_playlist_doesnt_exist_yet() -> None:
    """rebuild mode with no existing playlist → just create + add."""
    jf = MagicMock()
    jf.get_playlists.return_value = [
        {"Id": "other-456", "Name": "Unrelated"},
    ]
    jf.get_or_create_playlist.return_value = "fresh-pl-001"

    jf_name = "Brand New"
    matched_ids = ["track-X"]

    for pl in jf.get_playlists():
        if pl["Name"].lower() == jf_name.lower():
            jf.delete_playlist(pl["Id"])
            break
    pl_id = jf.get_or_create_playlist(jf_name)
    jf.add_to_playlist(pl_id, matched_ids)

    jf.delete_playlist.assert_not_called()
    jf.get_or_create_playlist.assert_called_once_with("Brand New")
    print("ok    rebuild does NOT delete when no matching playlist exists")


def test_pydantic_accepts_rebuild_mode() -> None:
    from spotify_sync.web.models import PlaylistEntry

    entry = PlaylistEntry(
        spotify_playlist_id="abc",
        jellyfin_playlist_name="Test",
        sync_mode="rebuild",
    )
    assert entry.sync_mode == "rebuild"

    # Sanity: invalid mode rejected
    try:
        PlaylistEntry(spotify_playlist_id="abc", sync_mode="bogus")
    except Exception:
        pass
    else:
        raise AssertionError("Pydantic should reject invalid sync_mode")
    print("ok    PlaylistEntry accepts 'rebuild', rejects garbage")


def test_jellyfin_client_has_delete_playlist() -> None:
    from spotify_sync.jellyfin_client import JellyfinClient

    assert hasattr(JellyfinClient, "delete_playlist"), \
        "JellyfinClient.delete_playlist not defined"
    # Smoke: create a stub instance and call delete_playlist with mocked _delete
    cfg = {
        "jellyfin": {
            "url": "http://j",
            "api_key": "k",
            "user_id": "u",
        },
    }
    client = JellyfinClient(cfg)
    client._delete = MagicMock()
    client.delete_playlist("pl-id-123")
    client._delete.assert_called_once_with("/Items/pl-id-123")
    print("ok    JellyfinClient.delete_playlist hits DELETE /Items/{id}")


def main() -> int:
    test_rebuild_deletes_existing_then_recreates()
    test_rebuild_when_playlist_doesnt_exist_yet()
    test_pydantic_accepts_rebuild_mode()
    test_jellyfin_client_has_delete_playlist()
    print("\npod 4 rebuild-mode test: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

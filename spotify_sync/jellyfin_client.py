"""Jellyfin client: library indexing, fuzzy track lookup, playlist CRUD."""

import logging
from typing import Optional

import requests

from .http_utils import http_get_with_retry
from .matcher import normalise, track_score

log = logging.getLogger(__name__)


class JellyfinClient:
    def __init__(self, cfg: dict):
        self.base = cfg["jellyfin"]["url"].rstrip("/")
        self.api_key = cfg["jellyfin"]["api_key"]
        self.user_id = cfg["jellyfin"]["user_id"]
        self.music_library_id: Optional[str] = cfg["jellyfin"].get("music_library_id")
        self.headers = {
            "X-Emby-Authorization": (
                f'MediaBrowser Client="spotify-sync", '
                f'Device="script", DeviceId="spotify-sync-01", '
                f'Version="1.0", Token="{self.api_key}"'
            ),
            "Content-Type": "application/json",
        }
        self.match_threshold: int = cfg.get("match_threshold", 80)
        self._library_cache: Optional[list[dict]] = None
        self._exact_index: dict[str, dict] = {}

    # ── HTTP helpers ──────────────────────────────────────────────────────

    def _get(self, path: str, **params) -> dict:
        r = http_get_with_retry(f"{self.base}{path}", self.headers, params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict | list | None = None, **params) -> requests.Response:
        r = requests.post(
            f"{self.base}{path}",
            headers=self.headers,
            params=params,
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        return r

    def _delete(self, path: str, **params) -> requests.Response:
        r = requests.delete(f"{self.base}{path}", headers=self.headers, params=params, timeout=30)
        r.raise_for_status()
        return r

    # ── Library ───────────────────────────────────────────────────────────

    def _build_index(self) -> None:
        """Fetch the music library and build exact + fuzzy lookup structures.
        Idempotent — subsequent calls are no-ops."""
        if self._library_cache is not None:
            return

        log.info("Fetching Jellyfin music library…")
        items: list[dict] = []
        start = 0
        limit = 1000
        params: dict = dict(
            IncludeItemTypes="Audio",
            Recursive=True,
            Fields="Name,AlbumArtist,Album,Artists",
        )
        if self.music_library_id:
            params["ParentId"] = self.music_library_id
            log.info("  Scoping to music library: %s", self.music_library_id)
        while True:
            params["StartIndex"] = start
            params["Limit"] = limit
            data = self._get(f"/Users/{self.user_id}/Items", **params)
            batch = data.get("Items", [])
            items.extend(batch)
            if start + limit >= data.get("TotalRecordCount", 0):
                break
            start += limit

        self._library_cache = items
        self._exact_index = {}
        for item in items:
            title = normalise(item.get("Name", ""))
            for artist in item.get("Artists", []):
                self._exact_index[f"{normalise(artist)}|{title}"] = item
            aa = item.get("AlbumArtist", "")
            if aa:
                self._exact_index.setdefault(f"{normalise(aa)}|{title}", item)

        log.info(
            "  Library ready: %d tracks, %d index keys",
            len(items), len(self._exact_index),
        )

    def find_track(self, title: str, artist: str) -> Optional[dict]:
        """Match a Spotify track against the Jellyfin library.

        Phase 1 — exact normalised "artist|title" lookup (O(1)).
        Phase 2 — fuzzy scan: title ≥75 and artist ≥65, then weighted combo
        must clear self.match_threshold (default 80).
        """
        self._build_index()

        key = f"{normalise(artist)}|{normalise(title)}"
        if key in self._exact_index:
            return self._exact_index[key]

        best_score = 0.0
        best_item: Optional[dict] = None

        for item in self._library_cache:  # type: ignore[union-attr]
            t_score = track_score(title, item.get("Name", ""))
            if t_score < 75:
                continue
            a_score = track_score(artist, " ".join(item.get("Artists", [])))
            if a_score < 65:
                continue
            combined = t_score * 0.65 + a_score * 0.35
            if combined > best_score:
                best_score = combined
                best_item = item

        if best_score >= self.match_threshold:
            return best_item
        return None

    # ── Playlists ─────────────────────────────────────────────────────────

    def get_playlists(self) -> list[dict]:
        data = self._get(
            f"/Users/{self.user_id}/Items",
            IncludeItemTypes="Playlist",
            Recursive=True,
        )
        return data.get("Items", [])

    def get_or_create_playlist(self, name: str) -> str:
        for pl in self.get_playlists():
            if pl["Name"].lower() == name.lower():
                return pl["Id"]
        log.info("  Creating Jellyfin playlist: %s", name)
        r = self._post(
            "/Playlists",
            payload={"Name": name, "UserId": self.user_id, "MediaType": "Audio"},
        )
        return r.json()["Id"]

    def get_playlist_items(self, playlist_id: str) -> list[dict]:
        data = self._get(f"/Playlists/{playlist_id}/Items", UserId=self.user_id)
        return data.get("Items", [])

    def add_to_playlist(self, playlist_id: str, item_ids: list[str]) -> None:
        if not item_ids:
            return
        self._post(
            f"/Playlists/{playlist_id}/Items",
            Ids=",".join(item_ids),
            UserId=self.user_id,
        )

    def remove_from_playlist(self, playlist_id: str, entry_ids: list[str]) -> None:
        """Remove items by their PlaylistItemId (not ItemId)."""
        if not entry_ids:
            return
        self._delete(
            f"/Playlists/{playlist_id}/Items",
            EntryIds=",".join(entry_ids),
        )

    def delete_playlist(self, playlist_id: str) -> None:
        """Delete a playlist entirely. Used by the ``rebuild`` sync mode
        to wipe-and-recreate so track ordering matches Spotify exactly
        and any drift (manual edits, stale items) is reset on every run.
        """
        self._delete(f"/Items/{playlist_id}")
        log.info("  Deleted Jellyfin playlist id=%s", playlist_id)

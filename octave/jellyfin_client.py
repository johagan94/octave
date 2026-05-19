"""Jellyfin client: library indexing, fuzzy track lookup, playlist CRUD,
cover art upload, persistent library index, and track-link cache integration."""

import json
import logging
from pathlib import Path
from typing import Optional

import requests

from .http_utils import http_get_with_retry
from .matcher import normalise, track_score

log = logging.getLogger(__name__)

INDEX_CACHE_PATH = Path("data/jellyfin_library_index.json")


class JellyfinClient:
    def __init__(self, cfg: dict, track_cache=None):
        self.base = cfg["jellyfin"]["url"].rstrip("/")
        self.api_key = cfg["jellyfin"]["api_key"]
        self.user_id = cfg["jellyfin"]["user_id"]
        self.music_library_id: Optional[str] = cfg["jellyfin"].get("music_library_id")
        self.headers = {
            "X-Emby-Authorization": (
                f'MediaBrowser Client="Octave", '
                f'Device="script", DeviceId="octave-01", '
                f'Version="3.0", Token="{self.api_key}"'
            ),
            "Content-Type": "application/json",
        }
        self.match_threshold: int = cfg.get("match_threshold", 80)
        self._library_cache: Optional[list[dict]] = None
        self._exact_index: dict[str, dict] = {}
        self._track_cache = track_cache  # TrackCache instance or None
        self._cache_hits = 0
        self._cache_misses = 0

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

    def _build_index(self, force_reload: bool = False) -> None:
        """Fetch the music library and build exact + fuzzy lookup structures.

        Idempotent — subsequent calls are no-ops unless ``force_reload=True``.
        Attempts to load a persistent index cache first for warm starts.
        """
        if self._library_cache is not None and not force_reload:
            return

        # Try persistent index cache
        if not force_reload:
            if self._load_persistent_index():
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
        self._save_persistent_index()

    def _save_persistent_index(self) -> None:
        """Persist the library index to disk for warm starts."""
        try:
            INDEX_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "items": [
                    {
                        "Id": i.get("Id"),
                        "Name": i.get("Name"),
                        "Artists": i.get("Artists", []),
                        "AlbumArtist": i.get("AlbumArtist", ""),
                        "Album": i.get("Album", ""),
                    }
                    for i in (self._library_cache or [])
                ],
            }
            INDEX_CACHE_PATH.with_suffix(".tmp").write_text(json.dumps(payload))
            INDEX_CACHE_PATH.with_suffix(".tmp").replace(INDEX_CACHE_PATH)
        except OSError as exc:
            log.debug("Failed to persist library index: %s", exc)

    def _load_persistent_index(self) -> bool:
        """Load a previously-saved library index. Returns True on success."""
        if not INDEX_CACHE_PATH.exists():
            return False
        try:
            data = json.loads(INDEX_CACHE_PATH.read_text())
            items = data.get("items", [])
            if not items:
                return False
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
                "  Library loaded from disk: %d tracks, %d index keys",
                len(items), len(self._exact_index),
            )
            return True
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            log.debug("Failed to load persistent index: %s — will rebuild", exc)
            return False

    def find_track(
        self, title: str, artist: str, spotify_id: Optional[str] = None
    ) -> Optional[dict]:
        """Match a Spotify track against the Jellyfin library.

        Phase 0 — track-link cache lookup (spotify_id → jellyfin_id).
        Phase 1 — exact normalised "artist|title" lookup (O(1)).
        Phase 2 — fuzzy scan: title ≥75 and artist ≥65, then weighted combo
        must clear self.match_threshold (default 80).
        """
        self._build_index()

        # Phase 0: track-link cache
        if spotify_id and self._track_cache:
            cached_jf_id = self._track_cache.get(spotify_id)
            if cached_jf_id:
                for item in self._library_cache:  # type: ignore[union-attr]
                    if item.get("Id") == cached_jf_id:
                        self._cache_hits += 1
                        return item
                self._track_cache.remove(spotify_id)

        key = f"{normalise(artist)}|{normalise(title)}"
        if key in self._exact_index:
            item = self._exact_index[key]
            if spotify_id and self._track_cache:
                self._track_cache.set(spotify_id, item["Id"])
            return item

        self._cache_misses += 1
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
            if spotify_id and self._track_cache and best_item:
                self._track_cache.set(spotify_id, best_item["Id"])
            return best_item
        return None

    def get_cache_stats(self) -> dict:
        return {"hits": self._cache_hits, "misses": self._cache_misses}

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

    def set_playlist_image(
        self, playlist_id: str, image_bytes: bytes, content_type: str = "image/jpeg"
    ) -> bool:
        """Upload a cover image for a playlist.

        POSTs raw image bytes to Jellyfin's /Items/{id}/Images/Primary.
        If the image is not JPEG, converts it using Pillow.
        Returns True on success, False on failure (graceful degradation).
        """
        try:
            # Normalise to JPEG (Spotify covers are usually JPEG, but may be
            # PNG/WebP). Best-effort if Pillow is available.
            try:
                from io import BytesIO

                from PIL import Image
                img = Image.open(BytesIO(image_bytes)).convert("RGB")
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=90)
                image_bytes = buf.getvalue()
            except ImportError:
                pass  # Pillow not installed; send original bytes
            except Exception as conv_exc:
                log.debug("  Cover convert skipped (%s); sending original", conv_exc)

            # Jellyfin's POST /Items/{id}/Images/{type} expects the body to
            # be the image BASE64-ENCODED as text — sending raw binary
            # returns HTTP 500 "Error processing request".
            import base64
            b64 = base64.b64encode(image_bytes)

            r = requests.post(
                f"{self.base}/Items/{playlist_id}/Images/Primary",
                headers={
                    "X-Emby-Authorization": self.headers["X-Emby-Authorization"],
                    "Content-Type": "image/jpeg",
                },
                data=b64,
                timeout=30,
            )
            if r.status_code in (200, 204):
                log.debug("  Cover art set for playlist id=%s", playlist_id)
                return True
            log.warning(
                "  Cover art upload returned %d for playlist id=%s: %s",
                r.status_code, playlist_id, r.text[:200],
            )
        except Exception as exc:
            log.warning("  Cover art upload failed for playlist id=%s: %s", playlist_id, exc)
        return False

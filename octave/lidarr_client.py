"""Lidarr client: artist/album lookup, add, monitor, search."""

import logging
import threading
from typing import Optional

import requests

from .http_utils import http_get_with_retry
from .matcher import best_match, normalise, score_pair

log = logging.getLogger(__name__)


class LidarrClient:
    def __init__(self, cfg: dict):
        self.base = cfg["lidarr"]["url"].rstrip("/")
        self.api_key = cfg["lidarr"]["api_key"]
        self.headers = {"X-Api-Key": self.api_key, "Content-Type": "application/json"}
        self.quality_profile_id: int = cfg["lidarr"].get("quality_profile_id", 1)
        self.metadata_profile_id: int = cfg["lidarr"].get("metadata_profile_id", 1)
        self._root_folder_override: Optional[str] = cfg["lidarr"].get("root_folder") or None
        self._root_folder_cache: Optional[str] = None
        self._artist_cache: Optional[list[dict]] = None
        self._album_cache: Optional[list[dict]] = None
        self._album_exact_index: dict[str, dict] = {}
        # Per-run cache: lowercase artist name → resolved Lidarr artist or None.
        # Sentinel ``...`` distinguishes "not yet looked up" from "looked up, no match".
        self._run_artist_cache: dict[str, Optional[dict]] = {}
        # Per-artist locks: prevents parallel threads from double-adding the same artist
        # when multiple missing albums from the same artist are processed concurrently.
        self._run_artist_lock_guard: threading.Lock = threading.Lock()
        self._run_artist_locks: dict[str, threading.Lock] = {}
        # Track which artists have already logged failures this run
        self._logged_failures: set[str] = set()

    # ── HTTP helpers ──────────────────────────────────────────────────────

    def _get(self, path: str, **params) -> list | dict:
        r = http_get_with_retry(
            f"{self.base}/api/v1{path}", self.headers, params, timeout=30
        )
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict) -> dict:
        r = requests.post(
            f"{self.base}/api/v1{path}", headers=self.headers, json=payload, timeout=30
        )
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, payload: dict) -> dict:
        r = requests.put(
            f"{self.base}/api/v1{path}", headers=self.headers, json=payload, timeout=30
        )
        r.raise_for_status()
        return r.json()

    # ── Root folder ───────────────────────────────────────────────────────

    @property
    def root_folder(self) -> str:
        if self._root_folder_override:
            return self._root_folder_override
        if self._root_folder_cache:
            return self._root_folder_cache
        folders = self._get("/rootfolder")
        if not folders:
            raise RuntimeError(
                "No root folders in Lidarr — add one in Settings → Media Management."
            )
        self._root_folder_cache = folders[0]["path"]
        log.info("  Auto-detected Lidarr root folder: %s", self._root_folder_cache)
        return self._root_folder_cache

    # ── Artist management ─────────────────────────────────────────────────

    def get_artists(self) -> list[dict]:
        if self._artist_cache is None:
            self._artist_cache = self._get("/artist")
        return self._artist_cache

    def find_artist_in_library(self, name: str) -> Optional[dict]:
        """Exact-then-fuzzy search of artists already in Lidarr."""
        name_l = name.lower()
        for a in self.get_artists():
            if a.get("artistName", "").lower() == name_l:
                return a
        result = best_match(
            name, self.get_artists(),
            key_fn=lambda a: a.get("artistName", ""),
            threshold=88,
            log_tag="library-artist",
        )
        return result.item if result else None

    def lookup_artist_mbid(self, mbid: str) -> Optional[dict]:
        """Look up artist in Lidarr using a MusicBrainz ID — most reliable."""
        try:
            results = self._get("/artist/lookup", term=f"lidarr:{mbid}")
        except requests.HTTPError as exc:
            log.warning("    Lidarr MBID artist lookup error: %s", exc)
            return None
        if not results:
            return None
        log.info("    Lidarr MBID lookup: %s", results[0].get("artistName"))
        return results[0]

    def lookup_artist_by_name(self, name: str) -> Optional[dict]:
        """Text-search Lidarr for an artist with up to three query variations:
        full name, first two words, first word only.
        Accepts the first result scoring ≥85.
        """
        queries = [name]
        words = name.split()
        if len(words) >= 3:
            queries.append(" ".join(words[:2]))
        if len(words) >= 2:
            queries.append(words[0])

        for query in queries:
            try:
                results = self._get("/artist/lookup", term=query)
            except requests.HTTPError as exc:
                log.warning("    Lidarr artist lookup error (%r): %s", query, exc)
                continue
            if not results:
                continue
            match = best_match(
                name, results,
                key_fn=lambda r: r.get("artistName", ""),
                threshold=85,
                log_tag=f"lookup({query!r})",
            )
            if match:
                log.info(
                    "    Text lookup matched %r → %r (score=%.1f, strategy=%s)",
                    query, match.item.get("artistName"), match.score, match.strategy,
                )
                return match.item

        log.warning("    No confident artist match found for %r in Lidarr", name)
        return None

    def add_artist(self, artist_info: dict) -> dict:
        """Add an artist to Lidarr.
        If Lidarr returns 400 (already exists), fetch and return the existing record.
        """
        payload = {
            "artistName":        artist_info["artistName"],
            "foreignArtistId":   artist_info["foreignArtistId"],
            "artistType":        artist_info.get("artistType", ""),
            "status":            artist_info.get("status", "continuing"),
            "qualityProfileId":  self.quality_profile_id,
            "metadataProfileId": self.metadata_profile_id,
            "rootFolderPath":    self.root_folder,
            "monitored":         True,
            "monitorNewItems":   "none",
            "addOptions": {
                "monitor":                "none",
                "searchForMissingAlbums": False,
            },
        }
        try:
            return self._post("/artist", payload)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 400:
                log.info("    Artist already exists in Lidarr — fetching existing record…")
                self._artist_cache = None
                existing = self.find_artist_in_library(artist_info["artistName"])
                if existing:
                    return existing
            raise

    def refresh_artist(self, artist_id: int) -> None:
        self._post("/command", {"name": "RefreshArtist", "artistId": artist_id})
        log.debug("    ↳ Lidarr: refresh queued for artist id=%d", artist_id)

    # ── Album management ──────────────────────────────────────────────────

    def get_albums(self) -> list[dict]:
        if self._album_cache is None:
            self._album_cache = self._get("/album")
        return self._album_cache

    def get_artist_albums(self, artist_id: int) -> list[dict]:
        return self._get("/album", artistId=artist_id)

    def _build_album_index(self) -> None:
        """Build O(1) lookup index for all albums. First checks exact, then score."""
        if self._album_exact_index:
            return
        for a in self.get_albums():
            title = normalise(a.get("title", ""))
            artist = normalise(
                a.get("artist", {}).get("artistName", "")
            )
            if title and artist:
                key = f"{artist}|{title}"
                if key not in self._album_exact_index:
                    self._album_exact_index[key] = a
        log.info("  Lidarr album index: %d entries", len(self._album_exact_index))

    def find_album_in_library(
        self, album_name: str, artist_name: str, spotify_artist_name: str = ""
    ) -> Optional[dict]:
        """Multi-strategy search across ALL Lidarr albums (indexed).

        Phase 1: exact index lookup (O(1)).
        Phase 2: fuzzy scan with compilation guard — if matched album has
        a different primary artist, don't accept it unless title score > 92.
        """
        self._build_album_index()

        an, aa = normalise(album_name), normalise(spotify_artist_name or artist_name)
        key = f"{aa}|{an}"
        if key in self._album_exact_index:
            return self._album_exact_index[key]

        best_score = 0.0
        best_item: Optional[dict] = None

        for a in self.get_albums():
            title_score, _ = score_pair(album_name, a.get("title", ""))
            artist_score, _ = score_pair(
                artist_name, a.get("artist", {}).get("artistName", "")
            )
            if title_score >= 85 and artist_score >= 75:
                combined = title_score * 0.55 + artist_score * 0.45
                # Compilation guard: penalise cross-artist matches
                if spotify_artist_name and title_score < 92:
                    lidarr_artist = normalise(a.get("artist", {}).get("artistName", ""))
                    if aa and lidarr_artist and aa != lidarr_artist:
                        combined -= 15
                if combined > best_score:
                    best_score = combined
                    best_item = a

        if best_item and best_score >= 70:
            return best_item
        return None

    def find_album_in_artist(
        self, artist_id: int, album_name: str, albums: Optional[list[dict]] = None
    ) -> Optional[dict]:
        """Find an album within a specific artist's catalogue.
        Tries thresholds 85 → 75 → 65 to maximise coverage.
        """
        if albums is None:
            albums = self.get_artist_albums(artist_id)
        if not albums:
            return None

        for threshold in (85, 75, 65):
            result = best_match(
                album_name, albums,
                key_fn=lambda a: a.get("title", ""),
                threshold=threshold,
                log_tag=f"album(t={threshold})",
            )
            if result:
                log.info(
                    "    Album matched: %r → %r (score=%.1f, strategy=%s, threshold=%d)",
                    album_name, result.item.get("title"),
                    result.score, result.strategy, threshold,
                )
                return result.item

        return None

    def lookup_album_mbid(self, mbid: str) -> Optional[dict]:
        """Look up a specific release in Lidarr by MusicBrainz release ID."""
        try:
            results = self._get("/album/lookup", term=f"lidarr:{mbid}")
        except requests.HTTPError as exc:
            log.warning("    Lidarr MBID album lookup error: %s", exc)
            return None
        return results[0] if results else None

    def monitor_and_search_album(self, lidarr_album_id: int) -> None:
        album = self._get(f"/album/{lidarr_album_id}")
        album["monitored"] = True
        self._put(f"/album/{lidarr_album_id}", album)
        self._post("/command", {"name": "AlbumSearch", "albumIds": [lidarr_album_id]})
        log.info("    ↳ Lidarr: monitoring + search triggered (album id=%d)", lidarr_album_id)

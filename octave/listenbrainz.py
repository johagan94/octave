"""ListenBrainz client: MBID resolution, popularity, recommendations.

No API key required for read operations (higher rate limits with key).
Rate limit: dynamic, indicated by X-RateLimit-* response headers.
"""

from __future__ import annotations

import logging
import os
import time
from itertools import islice
from typing import Optional

import requests

log = logging.getLogger(__name__)

BASE = "https://api.listenbrainz.org/1"
RATE_LIMIT_FLOOR = 0.5  # minimum seconds between requests


class ListenBrainzClient:

    def __init__(self, token: Optional[str] = None):
        self.token = token or _setting("LISTENBRAINZ_TOKEN") or None
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "octave-sync/3.0"
        if self.token:
            self._session.headers["Authorization"] = f"Token {self.token}"
        self._last_req = 0.0

    def validate_token(self) -> dict:
        """Validate the configured token and return ListenBrainz user details."""
        data = self._get("/validate-token")
        if not data.get("valid"):
            raise RuntimeError("ListenBrainz token is invalid")
        return data

    def _get(self, path: str, **params) -> dict:
        _enforce_rate(self)
        resp = self._session.get(f"{BASE}{path}", params=params, timeout=15)
        self._last_req = time.time()
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict:
        _enforce_rate(self)
        resp = self._session.post(f"{BASE}{path}", json=payload, timeout=20)
        self._last_req = time.time()
        resp.raise_for_status()
        return resp.json()

    def submit_listens(self, listens: list[dict], listen_type: str = "import") -> list[dict]:
        """Submit ListenBrainz listens in API-sized batches."""
        responses: list[dict] = []
        for batch in _chunks(listens, 1000):
            responses.append(self._post(
                "/submit-listens",
                {"listen_type": listen_type, "payload": batch},
            ))
        return responses

    def get_latest_import(self, user_name: str) -> int:
        """Return the newest imported-listen timestamp for a user."""
        data = self._get("/latest-import", user_name=user_name)
        return int(data.get("latest_import") or 0)

    def set_latest_import(self, timestamp: int) -> dict:
        """Persist the newest imported-listen timestamp after an import."""
        return self._post("/latest-import", {"ts": int(timestamp)})

    def create_playlist(self, title: str, recording_mbids: list[str]) -> dict:
        """Create a ListenBrainz playlist from MusicBrainz recording MBIDs."""
        return self._post("/playlist/create", _jspf_playlist(title, recording_mbids))

    def import_spotify_playlist_jspf(self, spotify_playlist_id: str) -> dict:
        """Ask ListenBrainz to convert a Spotify playlist to JSPF."""
        return self._get(f"/playlist/spotify/{spotify_playlist_id}/tracks")

    # ── MBID resolution ──────────────────────────────────────────────

    def lookup_metadata(
        self, artist: str, track: str, release: Optional[str] = None
    ) -> Optional[dict]:
        """Resolve artist + track → MusicBrainz IDs.

        Returns ``{recording_mbid, artist_mbids: [...], release_mbid, ...}``
        or None if no match.
        """
        payload: dict = {"artist_name": artist, "recording_name": track}
        if release:
            payload["release_name"] = release
        try:
            data = self._post("/metadata/lookup/", payload)
        except requests.HTTPError as exc:
            log.debug("ListenBrainz: metadata lookup failed: %s", exc)
            return None
        return data[0] if data else None

    def batch_lookup_metadata(
        self, queries: list[dict[str, str]]
    ) -> list[Optional[dict]]:
        """Resolve many artist+track pairs in one call.

        Each query: ``{"artist_name": "...", "recording_name": "...",
        "release_name": "..."}`` (release optional).
        Returns list of results in same order (None if unmatched).
        """
        if not queries:
            return []
        try:
            data = self._post("/metadata/lookup/", {"recordings": queries})
        except requests.HTTPError as exc:
            log.warning("ListenBrainz: batch lookup failed: %s", exc)
            return [None] * len(queries)
        return data if data else [None] * len(queries)

    # ── Popularity ───────────────────────────────────────────────────

    def get_recording_popularity(
        self, mbids: list[str]
    ) -> dict[str, dict]:
        """Batch: get global listen count + user count for recording MBIDs.

        Returns mapping of mbid → {total_listen_count, total_user_count}.
        """
        try:
            data = self._post("/popularity/recording", {"recording_mbids": mbids})
        except requests.HTTPError as exc:
            log.warning("ListenBrainz: popularity lookup failed: %s", exc)
            return {}
        return {
            entry["recording_mbid"]: {
                "listen_count": entry.get("total_listen_count", 0),
                "user_count": entry.get("total_user_count", 0),
            }
            for entry in data
            if entry.get("recording_mbid")
        }

    # ── Recommendations / similar ────────────────────────────────────

    def get_recommendations(
        self, user_name: str, count: int = 50, offset: int = 0
    ) -> list[dict]:
        """Collaborative filtering recommendations for a user.

        Returns list of ``{recording_mbid, score}`` sorted by score desc.
        Requires the user to have listening history in ListenBrainz.
        """
        try:
            data = self._get(
                f"/cf/recommendation/user/{user_name}/recording",
                count=count, offset=offset,
            )
        except requests.HTTPError as exc:
            log.warning("ListenBrainz: recommendations failed: %s", exc)
            return []
        return data.get("payload", {}).get("mbids", [])

    def get_similar_recordings(
        self, artist_mbid: str, mode: str = "easy", count: int = 50
    ) -> list[dict]:
        """LB Radio: get recordings similar to this artist.

        mode: 'easy' (popular), 'medium', 'hard' (obscure).
        """
        try:
            data = self._get(
                f"/lb-radio/artist/{artist_mbid}",
                mode=mode, count=count,
            )
        except requests.HTTPError as exc:
            log.warning("ListenBrainz: lb-radio failed: %s", exc)
            return []
        return data.get("payload", [])

    # ── Top stats ────────────────────────────────────────────────────

    def get_top_recordings(
        self, user_name: str, range_: str = "all_time", count: int = 100
    ) -> list[dict]:
        """Get top recordings for a user.

        range_: 'week', 'month', 'year', 'all_time'.
        Returns list of ``{track_name, artist_name, listen_count, recording_mbid}``.
        """
        try:
            data = self._get(
                f"/stats/user/{user_name}/recordings",
                range=range_, count=count,
            )
        except requests.HTTPError as exc:
            log.warning("ListenBrainz: stats failed: %s", exc)
            return []
        return data.get("payload", {}).get("recordings", [])


def _enforce_rate(client: ListenBrainzClient) -> None:
    gap = RATE_LIMIT_FLOOR - (time.time() - client._last_req)
    if gap > 0:
        time.sleep(gap)


def _setting(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from .web.settings import get_setting

        return get_setting(key).strip()
    except Exception:
        return ""


def _chunks(items: list[dict], size: int):
    iterator = iter(items)
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            break
        yield batch


def _jspf_playlist(title: str, recording_mbids: list[str]) -> dict:
    return {
        "playlist": {
            "title": title,
            "track": [
                {
                    "identifier": [
                        f"https://musicbrainz.org/recording/{mbid}",
                    ],
                }
                for mbid in recording_mbids
            ],
        },
    }

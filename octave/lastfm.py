"""Last.fm client: playcounts, similar tracks/artists, scrobble history.

Requires only an API key (free). Rate limit: undocumented — be reasonable
(single-digit requests per second). We enforce 200ms inter-request gap.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

BASE = "https://ws.audioscrobbler.com/2.0/"
RATE_GAP = 0.21


class LastFMClient:

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("LASTFM_API_KEY", "").strip()
        if not self.api_key:
            log.debug("LastFM: no API key set — client disabled")
        self._session = requests.Session()
        self._last_req = 0.0

    def _get(self, **params) -> dict:
        if not self.api_key:
            raise RuntimeError("LASTFM_API_KEY not configured")
        _enforce_rate(self)
        params.setdefault("api_key", self.api_key)
        params.setdefault("format", "json")
        resp = self._session.get(BASE, params=params, timeout=15)
        self._last_req = time.time()
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            code = data.get("error")
            msg = data.get("message", "Unknown error")
            if code == 29:
                log.warning("LastFM: rate limit exceeded — backing off")
                time.sleep(5)
            raise LastFMError(code, msg)
        return data

    # ── Top tracks ───────────────────────────────────────────────────

    def get_top_tracks(
        self, user: str, period: str = "overall", limit: int = 100
    ) -> list[dict]:
        """Get top tracks for a user with playcounts.

        period: overall | 7day | 1month | 3month | 6month | 12month.
        Returns list of ``{name, playcount, artist: {name, mbid}, mbid, url}``.
        """
        results: list[dict] = []
        page = 1
        while len(results) < limit:
            try:
                data = self._get(
                    method="user.getTopTracks",
                    user=user, period=period,
                    limit=min(200, limit - len(results)),
                    page=page,
                )
            except (requests.HTTPError, LastFMError) as exc:
                log.warning("LastFM: top tracks failed: %s", exc)
                break
            batch = data.get("toptracks", {}).get("track", [])
            if not batch:
                break
            results.extend(batch)
            page += 1
        return results[:limit]

    # ── Similar tracks / artists ─────────────────────────────────────

    def get_similar_tracks(
        self, artist: str, track: str, limit: int = 10
    ) -> list[dict]:
        """Get tracks similar to the given track.

        Returns list of ``{name, match (float), artist: {name, mbid}, mbid}``.
        """
        try:
            data = self._get(
                method="track.getSimilar",
                artist=artist, track=track,
                limit=min(limit, 100), autocorrect=1,
            )
        except (requests.HTTPError, LastFMError) as exc:
            log.debug("LastFM: similar tracks failed (%r - %r): %s", artist, track, exc)
            return []
        return data.get("similartracks", {}).get("track", [])

    def get_similar_artists(
        self, artist: str, limit: int = 10
    ) -> list[dict]:
        """Get artists similar to the given artist.

        Returns list of ``{name, match (float), mbid, url}``.
        """
        try:
            data = self._get(
                method="artist.getSimilar",
                artist=artist, limit=min(limit, 100), autocorrect=1,
            )
        except (requests.HTTPError, LastFMError) as exc:
            log.debug("LastFM: similar artists failed (%r): %s", artist, exc)
            return []
        return data.get("similarartists", {}).get("artist", [])

    # ── Track info ───────────────────────────────────────────────────

    def get_track_info(self, artist: str, track: str) -> Optional[dict]:
        """Full track metadata including playcount, listeners, mbid, tags.

        Returns ``{name, mbid, playcount, listeners, duration, album, toptags, wiki}``
        or None if not found.
        """
        try:
            data = self._get(
                method="track.getInfo",
                artist=artist, track=track, autocorrect=1,
            )
        except (requests.HTTPError, LastFMError) as exc:
            log.debug("LastFM: track info failed (%r - %r): %s", artist, track, exc)
            return None
        return data.get("track")

    def get_top_tags(self, artist: str, track: str) -> list[dict]:
        """Get top tags for a track."""
        info = self.get_track_info(artist, track)
        if not info:
            return []
        return info.get("toptags", {}).get("tag", [])

    def get_user_playcount(self, user: str, artist: str, track: str) -> int:
        """Get a specific user's playcount for a track (if in their top list)."""
        top = self.get_top_tracks(user, period="overall", limit=500)
        for t in top:
            t_artist = t.get("artist", {}).get("name", "")
            t_name = t.get("name", "")
            if t_artist.lower() == artist.lower() and t_name.lower() == track.lower():
                return int(t.get("playcount", 0))
        return 0


class LastFMError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"LastFM error {code}: {message}")


def _enforce_rate(client: LastFMClient) -> None:
    gap = RATE_GAP - (time.time() - client._last_req)
    if gap > 0:
        time.sleep(gap)

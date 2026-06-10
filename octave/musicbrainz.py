"""MusicBrainz resolver: maps Spotify IDs → MusicBrainz MBIDs.

MusicBrainz allows 1 request/second for anonymous clients; we enforce a
1.1s inter-request gap to stay comfortably under the limit.
"""

import logging
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)


class MusicBrainzResolver:
    BASE = "https://musicbrainz.org/ws/2"
    RATE_LIMIT = 1.1
    USER_AGENT = "octave-sync/3.0 (https://github.com/user/octave)"

    def __init__(self):
        self._last_req = 0.0
        self._artist_map: dict[str, Optional[str]] = {}
        self._album_map: dict[str, Optional[str]] = {}
        self._sess = requests.Session()
        self._sess.headers["User-Agent"] = self.USER_AGENT

    def _get(self, path: str, **params) -> dict:
        gap = self.RATE_LIMIT - (time.time() - self._last_req)
        if gap > 0:
            time.sleep(gap)
        params["fmt"] = "json"
        r = self._sess.get(f"{self.BASE}{path}", params=params, timeout=15)
        self._last_req = time.time()
        r.raise_for_status()
        return r.json()

    def get_artist_mbid(self, spotify_artist_id: str) -> Optional[str]:
        if spotify_artist_id in self._artist_map:
            return self._artist_map[spotify_artist_id]
        try:
            url = f"https://open.spotify.com/artist/{spotify_artist_id}"
            data = self._get("/url", resource=url, inc="artist-rels")
            for rel in data.get("relations", []):
                artist = rel.get("artist")
                if artist:
                    mbid = artist["id"]
                    log.debug("    MB artist MBID: %s → %s", spotify_artist_id, mbid)
                    self._artist_map[spotify_artist_id] = mbid
                    return mbid
        except Exception as exc:
            log.debug("    MB artist lookup failed (%s): %s", spotify_artist_id, exc)
        self._artist_map[spotify_artist_id] = None
        return None

    def get_album_mbid(self, spotify_album_id: str) -> Optional[str]:
        if spotify_album_id in self._album_map:
            return self._album_map[spotify_album_id]
        try:
            url = f"https://open.spotify.com/album/{spotify_album_id}"
            data = self._get("/url", resource=url, inc="release-rels")
            for rel in data.get("relations", []):
                release = rel.get("release")
                if release:
                    mbid = release["id"]
                    log.debug("    MB album MBID: %s → %s", spotify_album_id, mbid)
                    self._album_map[spotify_album_id] = mbid
                    return mbid
        except Exception as exc:
            log.debug("    MB album lookup failed (%s): %s", spotify_album_id, exc)
        self._album_map[spotify_album_id] = None
        return None

    def get_album_release_group_mbid(self, spotify_album_id: str) -> Optional[str]:
        """Resolve a Spotify album to a MusicBrainz **release-group** MBID.

        This is the ID Lidarr stores as ``foreignAlbumId``, so it can be matched
        exactly against a Lidarr artist's catalogue when fuzzy title matching
        fails (different edition/remaster/feat. tagging). Spotify album URLs in
        MusicBrainz attach to either a release-group or a specific release; we
        try the release-group relation first, then derive the group from a
        linked release. Results (including misses) are cached to respect the
        1 req/s rate limit.
        """
        cache_key = f"rg:{spotify_album_id}"
        if cache_key in self._album_map:
            return self._album_map[cache_key]
        mbid: Optional[str] = None
        try:
            url = f"https://open.spotify.com/album/{spotify_album_id}"
            data = self._get("/url", resource=url, inc="release-group-rels release-rels")
            for rel in data.get("relations", []):
                rg = rel.get("release_group") or rel.get("release-group")
                if rg and rg.get("id"):
                    mbid = rg["id"]
                    break
            if mbid is None:
                for rel in data.get("relations", []):
                    release = rel.get("release")
                    if release and release.get("id"):
                        rdata = self._get(f"/release/{release['id']}", inc="release-groups")
                        rg = rdata.get("release-group") or rdata.get("release_group")
                        if rg and rg.get("id"):
                            mbid = rg["id"]
                            break
            if mbid:
                log.debug("    MB release-group MBID: %s → %s", spotify_album_id, mbid)
        except Exception as exc:
            log.debug("    MB release-group lookup failed (%s): %s", spotify_album_id, exc)
        self._album_map[cache_key] = mbid
        return mbid

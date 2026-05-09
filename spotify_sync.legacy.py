#!/usr/bin/env python3
"""
spotify_sync.py — Sync Spotify playlists → Jellyfin + Lidarr

Workflow per run:
  1. Fetch all tracks from each configured Spotify playlist.
  2. Match tracks against your Jellyfin library (fuzzy title + artist match).
  3. Build / update a Jellyfin playlist with every matched track.
  4. For unmatched tracks, find the full album on Spotify and send the
     album to Lidarr (only if the album isn't already monitored/downloaded).
  5. Persist state so the next run picks up anything Lidarr has since finished.

First run: opens a browser for Spotify OAuth. Token is cached in
.spotify_token_cache and auto-refreshed on every subsequent run.

Requirements (install via pip):
    spotipy
    requests
    rapidfuzz
    python-dotenv
    (See requirements.txt)
"""

import json
import logging
import os
import sys
import time
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

import requests
import spotipy
from dotenv import load_dotenv
from rapidfuzz import fuzz
from spotipy.oauth2 import SpotifyOAuth

# ---------------------------------------------------------------------------
# Load .env (must happen before anything reads os.environ)
# ---------------------------------------------------------------------------
load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("spotify_sync.log"),
    ],
)
log = logging.getLogger(__name__)


def _require_env(key: str) -> str:
    """Return an env var or abort with a helpful message."""
    val = os.environ.get(key, "").strip()
    if not val:
        log.error(
            "Missing required environment variable: %s\n"
            "Add it to your .env file:  %s=your_value_here",
            key, key,
        )
        sys.exit(1)
    return val

# ---------------------------------------------------------------------------
# Config loader  (non-secret settings only — secrets live in .env)
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(os.environ.get("SYNC_CONFIG", "config.json"))


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        log.error("Config file not found: %s", CONFIG_PATH)
        sys.exit(1)
    with CONFIG_PATH.open() as fh:
        cfg = json.load(fh)

    # Inject credentials from environment into the config dict so the rest of
    # the code can still use cfg["spotify"]["client_id"] etc. unchanged.
    cfg.setdefault("spotify", {})
    cfg["spotify"]["client_id"]     = _require_env("SPOTIFY_CLIENT_ID")
    cfg["spotify"]["client_secret"] = _require_env("SPOTIFY_CLIENT_SECRET")
    cfg["spotify"]["redirect_uri"]  = os.environ.get(
        "SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback"
    )

    cfg.setdefault("jellyfin", {})
    cfg["jellyfin"]["api_key"] = _require_env("JELLYFIN_API_KEY")
    cfg["jellyfin"]["user_id"] = _require_env("JELLYFIN_USER_ID")
    # URL is non-secret so it stays in config.json, but env can override
    if os.environ.get("JELLYFIN_URL"):
        cfg["jellyfin"]["url"] = os.environ["JELLYFIN_URL"]

    cfg.setdefault("lidarr", {})
    cfg["lidarr"]["api_key"] = _require_env("LIDARR_API_KEY")
    if os.environ.get("LIDARR_URL"):
        cfg["lidarr"]["url"] = os.environ["LIDARR_URL"]

    return cfg


# ---------------------------------------------------------------------------
# State (persisted between runs)
# ---------------------------------------------------------------------------
STATE_PATH = Path("sync_state.json")


def load_state() -> dict:
    if STATE_PATH.exists():
        with STATE_PATH.open() as fh:
            return json.load(fh)
    return {"lidarr_requested_albums": {}, "jellyfin_playlists": {}}


def save_state(state: dict) -> None:
    with STATE_PATH.open("w") as fh:
        json.dump(state, fh, indent=2)


# ---------------------------------------------------------------------------
# Spotify helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Spotify OAuth helpers
# ---------------------------------------------------------------------------

SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative"
TOKEN_CACHE_PATH = Path(".spotify_token_cache")

# Scopes needed:
#   playlist-read-private       → your own private playlists
#   playlist-read-collaborative → collaborative playlists you follow


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that captures the ?code= from Spotify's redirect."""

    code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if "code" in params:
            _OAuthCallbackHandler.code = params["code"][0]
            body = b"<h2>Auth successful! You can close this tab.</h2>"
        elif "error" in params:
            _OAuthCallbackHandler.error = params["error"][0]
            body = b"<h2>Auth failed. Check the terminal for details.</h2>"
        else:
            body = b"<h2>Unexpected request.</h2>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):  # suppress default access logs
        pass


def _run_local_server(port: int) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), _OAuthCallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    return server


def make_spotify_client(cfg: dict) -> spotipy.Spotify:
    """
    Return an authenticated Spotify client using the Authorization Code flow.

    First run:
      • Opens a browser to Spotify's auth page.
      • Starts a local HTTP server to catch the redirect.
      • Saves the token to .spotify_token_cache.

    Subsequent runs:
      • Loads the cached token and silently refreshes it — no browser needed.
    """
    sp_cfg = cfg["spotify"]
    redirect_uri: str = sp_cfg["redirect_uri"]          # e.g. http://localhost:8888/callback
    port = int(urlparse(redirect_uri).port or 8888)

    auth_manager = SpotifyOAuth(
        client_id=sp_cfg["client_id"],
        client_secret=sp_cfg["client_secret"],
        redirect_uri=redirect_uri,
        scope=SPOTIFY_SCOPES,
        cache_path=str(TOKEN_CACHE_PATH),
        open_browser=False,   # we handle the browser ourselves for cleaner UX
    )

    # If a valid (or refreshable) token already exists, use it silently
    token_info = auth_manager.get_cached_token()
    if token_info and not auth_manager.is_token_expired(token_info):
        log.info("Spotify: using cached token (expires in %ds)",
                 token_info["expires_in"])
        return spotipy.Spotify(auth_manager=auth_manager)

    if token_info and auth_manager.is_token_expired(token_info):
        log.info("Spotify: refreshing expired token…")
        auth_manager.refresh_access_token(token_info["refresh_token"])
        return spotipy.Spotify(auth_manager=auth_manager)

    # ── First-time OAuth ────────────────────────────────────────────────────
    auth_url = auth_manager.get_authorize_url()
    log.info("=" * 60)
    log.info("SPOTIFY AUTH REQUIRED — first-time setup")
    log.info("=" * 60)
    log.info("Opening browser for Spotify login…")
    log.info("If the browser doesn't open, visit this URL manually:\n\n  %s\n", auth_url)

    # Start the local callback server before opening the browser
    _OAuthCallbackHandler.code = None
    _OAuthCallbackHandler.error = None
    server = _run_local_server(port)

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass  # headless server — user will paste the URL manually

    # Wait up to 5 minutes for the user to authenticate
    log.info("Waiting for Spotify callback on %s …", redirect_uri)
    deadline = time.time() + 300
    while _OAuthCallbackHandler.code is None and _OAuthCallbackHandler.error is None:
        if time.time() > deadline:
            log.error("Timed out waiting for Spotify auth. Re-run the script to try again.")
            sys.exit(1)
        time.sleep(0.25)

    if _OAuthCallbackHandler.error:
        log.error("Spotify auth error: %s", _OAuthCallbackHandler.error)
        sys.exit(1)

    code = _OAuthCallbackHandler.code
    log.info("Spotify: received auth code, exchanging for token…")
    auth_manager.get_access_token(code, as_dict=False, check_cache=False)
    log.info("Spotify: token saved to %s", TOKEN_CACHE_PATH)
    log.info("=" * 60)

    return spotipy.Spotify(auth_manager=auth_manager)


def get_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> list[dict]:
    """Return every track in a Spotify playlist (handles pagination)."""
    tracks = []
    result = sp.playlist_items(
        playlist_id,
        fields="items(track(id,name,artists(id,name),album(id,name,album_type,artists(id,name),total_tracks))),next",
        additional_types=["track"],
    )
    while result:
        for item in result.get("items", []):
            track = item.get("track")
            if track and track.get("id"):
                tracks.append(track)
        result = sp.next(result) if result.get("next") else None
    log.info("  Spotify playlist %s → %d tracks", playlist_id, len(tracks))
    return tracks


def get_album_tracks(sp: spotipy.Spotify, album_id: str) -> list[dict]:
    """Return all track objects for a Spotify album."""
    tracks = []
    result = sp.album_tracks(album_id)
    while result:
        tracks.extend(result["items"])
        result = sp.next(result) if result.get("next") else None
    return tracks


def _track_score(a: str, b: str) -> float:
    """
    Scoring for track title / artist matching.

    Only uses ratio (pure Levenshtein) and token_sort_ratio (word-order
    invariant). Deliberately excludes:
      - partial_ratio    → 'Bohemian' scores 100 against 'Bohemian Rhapsody'
      - token_set_ratio  → 'God' scores 100 against 'God Don't Make Mistakes'
      - WRatio           → internally uses the above two
    """
    an, bn = normalise(a), normalise(b)
    if an == bn:
        return 100.0
    return max(
        fuzz.ratio(an, bn),
        fuzz.token_sort_ratio(an, bn),
    )


# ---------------------------------------------------------------------------
# Resilient HTTP helper
# ---------------------------------------------------------------------------

def _http_get_with_retry(
    session_or_none,
    url: str,
    headers: dict,
    params: dict,
    timeout: int = 30,
    max_attempts: int = 5,
    backoff_base: float = 2.0,
) -> requests.Response:
    """
    GET with exponential backoff retry on connection errors and 5xx responses.
    Raises on the final failure.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
            if r.status_code < 500:
                return r
            # 5xx — server-side error, worth retrying
            log.warning(
                "HTTP %d on %s (attempt %d/%d)", r.status_code, url, attempt, max_attempts
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            log.warning(
                "Connection error on %s (attempt %d/%d): %s", url, attempt, max_attempts, exc
            )
            if attempt == max_attempts:
                raise
        if attempt < max_attempts:
            sleep = backoff_base ** attempt
            log.info("  Retrying in %.0fs…", sleep)
            time.sleep(sleep)
    # Final attempt already raised if it was a connection error;
    # if we reach here it was a persistent 5xx — raise it
    r.raise_for_status()
    return r  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Jellyfin helpers
# ---------------------------------------------------------------------------

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
        # Fuzzy-match threshold (0-100). Raise to be stricter.
        self.match_threshold: int = cfg.get("match_threshold", 80)
        self._library_cache: Optional[list[dict]] = None

    def _get(self, path: str, **params) -> dict:
        r = _http_get_with_retry(
            None, f"{self.base}{path}", self.headers, params, timeout=30
        )
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

    # -- Library --

    def _build_index(self) -> None:
        """
        Fetch the Jellyfin music library and build two lookup structures.
        Called once per run; subsequent calls are no-ops.

        _exact_index  — dict keyed by normalised "artist|title" → O(1) lookups
        _library_cache — flat list for fuzzy fallback scan
        """
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
            params["Limit"]      = limit
            data  = self._get(f"/Users/{self.user_id}/Items", **params)
            batch = data.get("Items", [])
            items.extend(batch)
            if start + limit >= data.get("TotalRecordCount", 0):
                break
            start += limit

        self._library_cache = items

        # Build exact-match index: "artist|title" → item
        self._exact_index: dict[str, dict] = {}
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
        """
        Match a Spotify track against the Jellyfin library.

        Phase 1 — Exact:  normalised "artist|title" dict lookup — O(1), no FP risk.
        Phase 2 — Fuzzy:  linear scan using only ratio + token_sort_ratio.
                          token_set_ratio and partial_ratio excluded — both cause
                          false positives on short strings sharing a single token.

        Title must score ≥ 75 and artist ≥ 65 independently before a combined
        score is computed. Combined must clear match_threshold (default 80).
        """
        self._build_index()

        # Phase 1 — exact
        key = f"{normalise(artist)}|{normalise(title)}"
        if key in self._exact_index:
            return self._exact_index[key]

        # Phase 2 — fuzzy fallback
        best_score = 0.0
        best_item  = None

        for item in self._library_cache:  # type: ignore[union-attr]
            t_score = _track_score(title,  item.get("Name", ""))
            if t_score < 75:
                continue  # fast reject before computing artist score

            a_score = _track_score(artist, " ".join(item.get("Artists", [])))
            if a_score < 65:
                continue

            combined = t_score * 0.65 + a_score * 0.35
            if combined > best_score:
                best_score = combined
                best_item  = item

        if best_score >= self.match_threshold:
            return best_item
        return None

    # -- Playlists --

    def get_playlists(self) -> list[dict]:
        data = self._get(
            f"/Users/{self.user_id}/Items",
            IncludeItemTypes="Playlist",
            Recursive=True,
        )
        return data.get("Items", [])

    def get_or_create_playlist(self, name: str) -> str:
        """Return the Jellyfin playlist ID, creating it if absent."""
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
        data = self._get(
            f"/Playlists/{playlist_id}/Items",
            UserId=self.user_id,
        )
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


# ---------------------------------------------------------------------------
# Text normalisation & multi-strategy matching
# ---------------------------------------------------------------------------

import re
import unicodedata
import datetime

_FEAT_RE = re.compile(
    r"\s*[\(\[](feat\.?|ft\.?|with|featuring)[^\)\]]*[\)\]]",
    flags=re.IGNORECASE,
)
_EDITION_RE = re.compile(
    r"\s*[\(\[](deluxe|explicit|clean|bonus|remaster(ed)?|anniversary"
    r"|expanded|special edition|re-?issue|re-?release|re-?master)[^\)\]]*[\)\]]",
    flags=re.IGNORECASE,
)
_ARTICLE_RE = re.compile(r"^(the|a|an)\s+", flags=re.IGNORECASE)
_PUNCT_RE   = re.compile(r"[^\w\s\-]")
_WS_RE      = re.compile(r"\s+")


def normalise(s: str) -> str:
    """Comprehensive text normalisation for matching."""
    # Unicode decompose then re-encode as ASCII where possible
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = _FEAT_RE.sub("", s)
    s = _EDITION_RE.sub("", s)
    s = _ARTICLE_RE.sub("", s.strip())
    s = _PUNCT_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip().lower()
    return s


class MatchResult:
    __slots__ = ("item", "score", "strategy")

    def __init__(self, item: dict, score: float, strategy: str):
        self.item     = item
        self.score    = score
        self.strategy = strategy

    def __repr__(self) -> str:
        return f"<MatchResult score={self.score:.1f} strategy={self.strategy}>"


def score_pair(a: str, b: str) -> tuple[float, str]:
    """
    Compute a composite match score between two strings using multiple strategies.

    Strategies (all from rapidfuzz, all operate on normalised strings):
      ratio        — pure Levenshtein edit-distance ratio
      partial      — best alignment of shorter string inside longer
      token_sort   — sort tokens alphabetically then compare (handles word order)
      token_set    — set-based token overlap (robust to repeated words)
      WRatio       — rapidfuzz's weighted combination (generally best single scorer)

    Also retries every strategy on the *raw* (un-normalised) lowercase strings
    so that normalisation can never make a match worse.

    Returns (best_score, strategy_name).
    """
    an, bn = normalise(a), normalise(b)
    ar, br = a.lower().strip(), b.lower().strip()

    if an == bn or ar == br:
        return 100.0, "exact"

    candidates: dict[str, float] = {
        "ratio_norm":       fuzz.ratio(an, bn),
        "partial_norm":     fuzz.partial_ratio(an, bn),
        "token_sort_norm":  fuzz.token_sort_ratio(an, bn),
        "token_set_norm":   fuzz.token_set_ratio(an, bn),
        "WRatio_norm":      fuzz.WRatio(an, bn),
        "ratio_raw":        fuzz.ratio(ar, br),
        "partial_raw":      fuzz.partial_ratio(ar, br),
        "token_sort_raw":   fuzz.token_sort_ratio(ar, br),
        "token_set_raw":    fuzz.token_set_ratio(ar, br),
        "WRatio_raw":       fuzz.WRatio(ar, br),
    }

    best_strategy = max(candidates, key=candidates.__getitem__)
    return candidates[best_strategy], best_strategy


def best_match(
    needle: str,
    candidates: list[dict],
    key_fn,
    threshold: float,
    log_tag: str = "",
) -> Optional[MatchResult]:
    """
    Score every candidate and return the best one above threshold.
    Logs the top-3 results at DEBUG level for diagnosability.
    """
    results: list[MatchResult] = []
    for c in candidates:
        val  = key_fn(c)
        sc, st = score_pair(needle, val)
        results.append(MatchResult(c, sc, st))

    results.sort(key=lambda r: r.score, reverse=True)

    for i, r in enumerate(results[:3]):
        log.debug(
            "    %s candidate #%d: %r  score=%.1f  strategy=%s",
            log_tag, i + 1, key_fn(r.item), r.score, r.strategy,
        )

    if results and results[0].score >= threshold:
        return results[0]
    return None


# ---------------------------------------------------------------------------
# MusicBrainz resolver  (Spotify ID → MusicBrainz MBID)
# ---------------------------------------------------------------------------

class MusicBrainzResolver:
    """
    Resolves Spotify artist / album IDs to MusicBrainz MBIDs via the
    MusicBrainz URL-relationship API.  Results are cached in memory.

    MusicBrainz allows 1 request/second for anonymous clients; we
    enforce a 1.1 s inter-request gap to stay comfortably under the limit.
    """

    BASE        = "https://musicbrainz.org/ws/2"
    RATE_LIMIT  = 1.1   # seconds between requests
    USER_AGENT  = "spotify-jellyfin-sync/2.0 (https://github.com/user/spotify-sync)"

    def __init__(self):
        self._last_req   = 0.0
        self._artist_map: dict[str, Optional[str]] = {}   # spotify_id → mbid
        self._album_map:  dict[str, Optional[str]] = {}
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
            url  = f"https://open.spotify.com/artist/{spotify_artist_id}"
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
            url  = f"https://open.spotify.com/album/{spotify_album_id}"
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


# ---------------------------------------------------------------------------
# Lidarr client
# ---------------------------------------------------------------------------

class LidarrClient:
    def __init__(self, cfg: dict):
        self.base     = cfg["lidarr"]["url"].rstrip("/")
        self.api_key  = cfg["lidarr"]["api_key"]
        self.headers  = {"X-Api-Key": self.api_key, "Content-Type": "application/json"}
        self.quality_profile_id:  int = cfg["lidarr"].get("quality_profile_id", 1)
        self.metadata_profile_id: int = cfg["lidarr"].get("metadata_profile_id", 1)
        self._root_folder_override: Optional[str] = cfg["lidarr"].get("root_folder") or None
        self._root_folder_cache:    Optional[str] = None
        self._artist_cache: Optional[list[dict]]  = None
        self._album_cache:  Optional[list[dict]]  = None
        # Per-run cache: lowercase artist name → resolved Lidarr artist or None
        self._run_artist_cache: dict[str, Optional[dict]] = {}

    # ── HTTP helpers ──────────────────────────────────────────────────────

    def _get(self, path: str, **params) -> list | dict:
        r = _http_get_with_retry(
            None, f"{self.base}/api/v1{path}", self.headers, params, timeout=30
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
            raise RuntimeError("No root folders in Lidarr — add one in Settings → Media Management.")
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
        """
        Text-search Lidarr for an artist.  Tries up to three query variations:
          1. Full name
          2. First two words (handles 'Conway the Machine' → 'Conway Machine')
          3. First word only
        Accepts the first result scoring ≥ 85 across the multi-strategy matcher.
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
        """
        Add an artist to Lidarr.
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
                "monitor":                 "none",
                "searchForMissingAlbums":  False,
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

    def find_album_in_library(self, album_name: str, artist_name: str) -> Optional[dict]:
        """Multi-strategy search across ALL Lidarr albums (cached)."""
        for a in self.get_albums():
            title_score, _ = score_pair(album_name, a.get("title", ""))
            artist_score, _ = score_pair(
                artist_name, a.get("artist", {}).get("artistName", "")
            )
            if title_score >= 85 and artist_score >= 75:
                return a
        return None

    def find_album_in_artist(
        self, artist_id: int, album_name: str, albums: Optional[list[dict]] = None
    ) -> Optional[dict]:
        """
        Find an album within a specific artist's Lidarr catalogue.
        Accepts pre-fetched album list to avoid redundant API calls.
        Tries with and without edition/feat cleaning for maximum coverage.
        """
        if albums is None:
            albums = self.get_artist_albums(artist_id)
        if not albums:
            return None

        # Try strict threshold first, then relax
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def primary_artist(track: dict) -> str:
    artists = track.get("artists", [])
    return artists[0]["name"] if artists else ""

def primary_artist_id(track: dict) -> str:
    artists = track.get("artists", [])
    return artists[0]["id"] if artists else ""


# ---------------------------------------------------------------------------
# Core album request logic
# ---------------------------------------------------------------------------

def request_album_in_lidarr(
    lidarr: LidarrClient,
    mb: MusicBrainzResolver,
    spotify_album_id:    str,
    spotify_album_name:  str,
    spotify_artist_id:   str,
    spotify_artist_name: str,
    state: dict,
) -> None:
    """
    Non-blocking state machine. Each call does only what it can right now
    and records where to pick up next run. No sleeps, no polling loops.

    States
    ──────
    (none)                  → fresh album, start from scratch
    artist_added            → artist was added last run, check if albums appeared
    artist_not_found        → lookup failed, retry next run
    album_pending           → artist has albums but target not found yet, retry
    already_monitored       → done
    requested               → done
    """
    requested = state["lidarr_requested_albums"]
    entry     = requested.get(spotify_album_id, {})
    status    = entry.get("status")

    # Terminal states — nothing more to do
    if status in ("already_monitored", "requested"):
        return

    log.info("  → [%s] %s – %s", status or "new", spotify_artist_name, spotify_album_name)

    # ── Check if album already exists in Lidarr library ───────────────────
    existing = lidarr.find_album_in_library(spotify_album_name, spotify_artist_name)
    if existing:
        if not existing.get("monitored"):
            lidarr.monitor_and_search_album(existing["id"])
            requested[spotify_album_id] = {"status": "requested", "lidarr_id": existing["id"]}
            log.info("    ✓ Found in library, now monitored (id=%d)", existing["id"])
        else:
            log.info("    Already monitored (id=%d)", existing["id"])
            requested[spotify_album_id] = {"status": "already_monitored", "lidarr_id": existing["id"]}
        save_state(state)
        return

    # ── Resolve / add the artist ───────────────────────────────────────────
    artist_key    = spotify_artist_name.lower()
    lidarr_artist = lidarr._run_artist_cache.get(artist_key, ...)

    if lidarr_artist is ...:
        lidarr_artist = lidarr.find_artist_in_library(spotify_artist_name)

        if lidarr_artist is None:
            artist_info   = None
            artist_mbid   = mb.get_artist_mbid(spotify_artist_id) if spotify_artist_id else None

            if artist_mbid:
                log.info("    MB MBID resolved: %s", artist_mbid)
                artist_info = lidarr.lookup_artist_mbid(artist_mbid)

            if artist_info is None:
                log.info("    Trying text search: %s", spotify_artist_name)
                artist_info = lidarr.lookup_artist_by_name(spotify_artist_name)

            if artist_info is None:
                log.warning("    Artist not found — will retry next run")
                lidarr._run_artist_cache[artist_key] = None
                requested[spotify_album_id] = {"status": "artist_not_found", "run": state["current_run"]}
                save_state(state)
                return

            log.info("    Adding artist: %s", artist_info["artistName"])
            try:
                lidarr_artist = lidarr.add_artist(artist_info)
                lidarr._artist_cache = None
                lidarr.refresh_artist(lidarr_artist["id"])
                log.info("    Artist added (id=%d), refresh triggered — albums will appear next run", lidarr_artist["id"])
            except requests.HTTPError as exc:
                log.error("    Add artist failed: %s", exc)
                lidarr._run_artist_cache[artist_key] = None
                requested[spotify_album_id] = {"status": "artist_add_failed", "run": state["current_run"]}
                save_state(state)
                return

        lidarr._run_artist_cache[artist_key] = lidarr_artist

    if lidarr_artist is None:
        # Artist lookup failed on a previous run — re-attempt next run
        if entry.get("run") != state["current_run"]:
            del requested[spotify_album_id]   # clear so it retries fresh
        save_state(state)
        return

    artist_id = lidarr_artist["id"]

    # ── Try to find the album right now ────────────────────────────────────
    albums = lidarr.get_artist_albums(artist_id)

    if not albums:
        # Artist exists but albums haven't been indexed yet
        # Trigger another refresh and come back next run
        log.info("    Artist has no albums yet — triggering refresh, will check next run")
        try:
            lidarr.refresh_artist(artist_id)
        except Exception as exc:
            log.warning("    Refresh error: %s", exc)
        requested[spotify_album_id] = {
            "status":    "artist_added",
            "artist_id": artist_id,
            "run":       state["current_run"],
        }
        save_state(state)
        return

    album = lidarr.find_album_in_artist(artist_id, spotify_album_name, albums)

    if album is None:
        log.warning(
            "    %d albums found for artist but %r not matched — retry next run",
            len(albums), spotify_album_name,
        )
        # Trigger a refresh in case Lidarr's catalogue is stale
        try:
            lidarr.refresh_artist(artist_id)
        except Exception:
            pass
        requested[spotify_album_id] = {
            "status":    "album_pending",
            "artist_id": artist_id,
            "run":       state["current_run"],
        }
        save_state(state)
        return

    # ── Monitor + search ───────────────────────────────────────────────────
    lidarr.monitor_and_search_album(album["id"])
    requested[spotify_album_id] = {"status": "requested", "lidarr_id": album["id"]}
    log.info(
        "    ✓ Queued: %s – %s (lidarr_id=%d)",
        spotify_artist_name, album.get("title", spotify_album_name), album["id"],
    )
    save_state(state)


# ---------------------------------------------------------------------------
# Playlist sync
# ---------------------------------------------------------------------------

def sync_playlist(
    playlist_cfg: dict,
    sp: spotipy.Spotify,
    jf: JellyfinClient,
    lidarr: LidarrClient,
    mb: MusicBrainzResolver,
    state: dict,
    playlist_num: int,
    playlist_total: int,
) -> None:
    spotify_id   = playlist_cfg["spotify_playlist_id"]
    jf_name      = playlist_cfg.get("jellyfin_playlist_name", f"Spotify – {spotify_id}")
    sync_mode    = playlist_cfg.get("sync_mode", "add_only")

    log.info("═" * 60)
    log.info(
        "Playlist %d/%d: %s  [%s]",
        playlist_num, playlist_total, jf_name, sync_mode,
    )

    sp_tracks = get_playlist_tracks(sp, spotify_id)
    if not sp_tracks:
        log.warning("  Empty playlist, skipping.")
        return

    # Build the library index once (no-op after first playlist)
    jf._build_index()

    # ── Match against Jellyfin ─────────────────────────────────────────────
    matched_ids: list[str] = []
    missing: list[dict]    = []

    for track in sp_tracks:
        title  = track["name"]
        artist = primary_artist(track)
        jf_item = jf.find_track(title, artist)
        if jf_item:
            matched_ids.append(jf_item["Id"])
        else:
            missing.append(track)

    log.info(
        "  Matched %d / %d   Missing %d",
        len(matched_ids), len(sp_tracks), len(missing),
    )

    # Deduplicate: multiple Spotify tracks can match the same Jellyfin item
    seen: set[str] = set()
    unique_matched: list[str] = []
    for iid in matched_ids:
        if iid not in seen:
            seen.add(iid)
            unique_matched.append(iid)
    if len(unique_matched) < len(matched_ids):
        log.info("  Deduplicated %d → %d unique Jellyfin items",
                 len(matched_ids), len(unique_matched))
    matched_ids = unique_matched

    # ── Update Jellyfin playlist ───────────────────────────────────────────
    pl_id = jf.get_or_create_playlist(jf_name)

    existing_items   = jf.get_playlist_items(pl_id)
    existing_item_ids = {i["Id"] for i in existing_items}

    if sync_mode == "full_sync":
        matched_set = set(matched_ids)
        to_remove   = [
            i["PlaylistItemId"]
            for i in existing_items
            if i["Id"] not in matched_set
        ]
        if to_remove:
            log.info("  Removing %d stale tracks from Jellyfin playlist", len(to_remove))
            jf.remove_from_playlist(pl_id, to_remove)

    new_ids = [iid for iid in matched_ids if iid not in existing_item_ids]
    if new_ids:
        log.info("  Adding %d new tracks to Jellyfin playlist", len(new_ids))
        for i in range(0, len(new_ids), 100):
            jf.add_to_playlist(pl_id, new_ids[i : i + 100])
    else:
        log.info("  Jellyfin playlist already up to date.")

    # ── Send missing albums to Lidarr ──────────────────────────────────────
    if not missing:
        return

    seen_albums: set[str] = set()
    current_run = state.get("current_run", "")

    for track in missing:
        album = track.get("album", {})

        # Only request full albums (not singles or EPs from Spotify's perspective)
        if album.get("album_type") not in (None, "album", "compilation"):
            log.debug(
                "  Skipping non-album release: %s (%s)",
                album.get("name"), album.get("album_type"),
            )
            continue

        album_id = album.get("id")
        if not album_id or album_id in seen_albums:
            continue
        seen_albums.add(album_id)

        album_name  = album.get("name", "Unknown Album")
        album_artists = album.get("artists", [])
        artist_name = album_artists[0]["name"] if album_artists else primary_artist(track)
        artist_id   = album_artists[0].get("id", "") if album_artists else primary_artist_id(track)

        # Clear retryable states from previous runs so they get re-attempted
        existing = state["lidarr_requested_albums"].get(album_id, {})
        if (
            existing.get("status") in ("artist_not_found", "artist_add_failed",
                                        "artist_added", "album_pending")
            and existing.get("run") != current_run
        ):
            log.info("  Retrying [%s] from prev run: %s – %s",
                     existing["status"], artist_name, album_name)
            del state["lidarr_requested_albums"][album_id]

        request_album_in_lidarr(
            lidarr, mb,
            album_id, album_name,
            artist_id, artist_name,
            state,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    cfg   = load_config()
    state = load_state()
    state["current_run"] = datetime.datetime.utcnow().isoformat()

    sp     = make_spotify_client(cfg)
    jf     = JellyfinClient(cfg)
    lidarr = LidarrClient(cfg)
    mb     = MusicBrainzResolver()

    playlists = cfg.get("playlists", [])
    if not playlists:
        log.error("No playlists defined in config.json")
        sys.exit(1)

    total = len(playlists)
    for n, pl_cfg in enumerate(playlists, 1):
        try:
            sync_playlist(pl_cfg, sp, jf, lidarr, mb, state, n, total)
        except Exception as exc:
            log.exception(
                "Error syncing playlist %s: %s",
                pl_cfg.get("spotify_playlist_id"), exc,
            )

    log.info("═" * 60)
    log.info("Sync complete.")
    save_state(state)


if __name__ == "__main__":
    main()

    def __init__(self, cfg: dict):
        self.base = cfg["lidarr"]["url"].rstrip("/")
        self.api_key = cfg["lidarr"]["api_key"]
        self.headers = {"X-Api-Key": self.api_key, "Content-Type": "application/json"}
        self.quality_profile_id: int = cfg["lidarr"].get("quality_profile_id", 1)
        self.metadata_profile_id: int = cfg["lidarr"].get("metadata_profile_id", 1)
        # root_folder is optional in config — auto-detected from Lidarr if absent
        self._root_folder_override: Optional[str] = cfg["lidarr"].get("root_folder")
        self._root_folder_cache: Optional[str] = None
        self._album_cache: Optional[list[dict]] = None
        self._artist_cache: Optional[list[dict]] = None
        # Per-run cache: artist name (lower) → resolved Lidarr artist dict or None
        # Prevents repeated lookups/add attempts for the same artist within one run
        self._run_artist_cache: dict[str, Optional[dict]] = {}

    # -- HTTP helpers --

    def _get(self, path: str, **params) -> list | dict:
        r = requests.get(
            f"{self.base}/api/v1{path}", headers=self.headers, params=params, timeout=30
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

    # -- Root folder (auto-detected) --

    @property
    def root_folder(self) -> str:
        """Return the configured root folder, or auto-detect from Lidarr."""
        if self._root_folder_override:
            return self._root_folder_override
        if self._root_folder_cache:
            return self._root_folder_cache
        folders = self._get("/rootfolder")
        if not folders:
            raise RuntimeError("No root folders configured in Lidarr. Add one in Settings → Media Management.")
        self._root_folder_cache = folders[0]["path"]
        log.info("  Auto-detected Lidarr root folder: %s", self._root_folder_cache)
        return self._root_folder_cache

    # -- Artist management --

    def get_artists(self) -> list[dict]:
        if self._artist_cache is None:
            self._artist_cache = self._get("/artist")
        return self._artist_cache

    def find_artist_in_library(self, name: str) -> Optional[dict]:
        """Exact-then-fuzzy match against artists already in Lidarr."""
        name_lower = name.lower()
        # Exact match first
        for a in self.get_artists():
            if a.get("artistName", "").lower() == name_lower:
                return a
        # Fuzzy fallback (threshold 90 to avoid false positives)
        best_score, best = 0, None
        for a in self.get_artists():
            score = fuzz.token_sort_ratio(a.get("artistName", "").lower(), name_lower)
            if score > best_score:
                best_score, best = score, a
        return best if best_score >= 90 else None

    def lookup_artist_by_name(self, name: str) -> Optional[dict]:
        """Query Lidarr's MusicBrainz-backed search for an artist by name.
        Returns the best-matching result, or None if nothing scores above 85."""
        try:
            results = self._get("/artist/lookup", term=name)
        except requests.HTTPError as exc:
            log.warning("    Lidarr artist lookup HTTP error: %s", exc)
            return None
        if not results:
            return None
        name_lower = name.lower()
        scored = sorted(
            results,
            key=lambda r: fuzz.token_sort_ratio(r.get("artistName", "").lower(), name_lower),
            reverse=True,
        )
        # Log top 3 candidates so mismatches are easy to spot
        for i, r in enumerate(scored[:3]):
            s = fuzz.token_sort_ratio(r.get("artistName", "").lower(), name_lower)
            log.debug("    Lookup candidate #%d: %s (score=%d)", i + 1, r.get("artistName"), s)
        best = scored[0]
        score = fuzz.token_sort_ratio(best.get("artistName", "").lower(), name_lower)
        if score >= 85:
            log.info("    Lookup matched: %s (score=%d)", best.get("artistName"), score)
            return best
        log.warning(
            "    Lookup best match '%s' (score=%d) is below threshold for '%s' — skipping",
            best.get("artistName"), score, name,
        )
        return None

    def lookup_artist_by_mbid(self, mbid: str) -> Optional[dict]:
        """Fetch artist info from Lidarr using a MusicBrainz ID (most reliable)."""
        try:
            results = self._get("/artist/lookup", term=f"lidarr:{mbid}")
        except requests.HTTPError as exc:
            log.warning("    Lidarr MBID artist lookup error: %s", exc)
            return None
        return results[0] if results else None

    def add_artist(self, artist_info: dict) -> dict:
        """Add an artist to Lidarr.
        If Lidarr returns 400 (artist already exists), fetch and return the existing record."""
        payload = {
            "artistName": artist_info["artistName"],
            "foreignArtistId": artist_info["foreignArtistId"],
            "artistType": artist_info.get("artistType", ""),
            "status": artist_info.get("status", "continuing"),
            "qualityProfileId": self.quality_profile_id,
            "metadataProfileId": self.metadata_profile_id,
            "rootFolderPath": self.root_folder,
            "monitored": True,
            "monitorNewItems": "none",
            "addOptions": {
                "monitor": "none",
                "searchForMissingAlbums": False,
            },
        }
        try:
            return self._post("/artist", payload)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 400:
                # Artist already exists in Lidarr — fetch it
                log.info("    Artist already in Lidarr (409/400), fetching existing record…")
                self._artist_cache = None  # force refresh
                existing = self.find_artist_in_library(artist_info["artistName"])
                if existing:
                    return existing
            raise

    # -- Album management --

    def get_artist_albums(self, artist_id: int) -> list[dict]:
        """Fetch all albums Lidarr knows about for a specific artist."""
        return self._get("/album", artistId=artist_id)

    def find_album_in_artist(self, artist_id: int, album_name: str) -> Optional[dict]:
        """Fuzzy-match an album name against a specific artist's catalogue in Lidarr."""
        albums = self.get_artist_albums(artist_id)
        if not albums:
            return None
        al = album_name.lower()
        scored = sorted(
            albums,
            key=lambda a: fuzz.token_sort_ratio(a.get("title", "").lower(), al),
            reverse=True,
        )
        best = scored[0]
        score = fuzz.token_sort_ratio(best.get("title", "").lower(), al)
        log.debug(
            "    Best album match: %s (score=%d, wanted=%s)",
            best.get("title"), score, album_name,
        )
        return best if score >= 75 else None

    def find_album_in_library(self, album_name: str, artist_name: str) -> Optional[dict]:
        """Search all Lidarr albums (cached) for a title+artist match."""
        if self._album_cache is None:
            self._album_cache = self._get("/album")
        al, ar = album_name.lower(), artist_name.lower()
        best_score, best = 0, None
        for a in self._album_cache:
            t = fuzz.token_sort_ratio(a.get("title", "").lower(), al)
            aa = fuzz.token_sort_ratio(
                a.get("artist", {}).get("artistName", "").lower(), ar
            )
            score = (t + aa) / 2
            if score > best_score:
                best_score, best = score, a
        return best if best_score >= 80 else None

    def monitor_and_search_album(self, lidarr_album_id: int) -> None:
        """Enable monitoring on an existing Lidarr album and trigger a search."""
        album = self._get(f"/album/{lidarr_album_id}")
        album["monitored"] = True
        self._put(f"/album/{lidarr_album_id}", album)
        self._post("/command", {"name": "AlbumSearch", "albumIds": [lidarr_album_id]})
        log.info("    ↳ Lidarr: monitoring + searching album id=%d", lidarr_album_id)

    def refresh_artist(self, artist_id: int) -> None:
        """Tell Lidarr to refresh an artist's metadata (populates albums list)."""
        self._post("/command", {"name": "RefreshArtist", "artistId": artist_id})
        log.info("    ↳ Lidarr: refreshing artist id=%d", artist_id)


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

import re as _re

# Patterns that appear in Spotify names but not MusicBrainz titles
_FEAT_PATTERN = _re.compile(
    r"\s*[\(\[](feat\.?|ft\.?|with|featuring)[^\)\]]*[\)\]]",
    flags=_re.IGNORECASE,
)
_TRAILING_NOISE = _re.compile(
    r"\s*[\(\[](deluxe|explicit|clean|bonus|remaster(ed)?|anniversary)[^\)\]]*[\)\]]",
    flags=_re.IGNORECASE,
)


def clean_name(s: str) -> str:
    """Strip featured-artist tags and edition noise for better fuzzy matching."""
    s = _FEAT_PATTERN.sub("", s)
    s = _TRAILING_NOISE.sub("", s)
    return s.strip()


def normalise(s: str) -> str:
    return clean_name(s).lower()


def primary_artist(track: dict) -> str:
    artists = track.get("artists", [])
    return artists[0]["name"] if artists else ""


def request_album_in_lidarr(
    lidarr: LidarrClient,
    spotify_album_id: str,
    spotify_album_name: str,
    spotify_artist_name: str,
    state: dict,
) -> None:
    """Ensure the full album is queued in Lidarr exactly once."""
    requested = state["lidarr_requested_albums"]
    if spotify_album_id in requested:
        log.debug("    Album already requested: %s", spotify_album_name)
        return

    log.info("  → Requesting album in Lidarr: %s – %s", spotify_artist_name, spotify_album_name)

    # ── Step 1: Check if the album is already known to Lidarr ──────────────
    existing_album = lidarr.find_album_in_library(spotify_album_name, spotify_artist_name)
    if existing_album:
        if not existing_album.get("monitored"):
            lidarr.monitor_and_search_album(existing_album["id"])
            requested[spotify_album_id] = {"status": "existing_now_monitored", "lidarr_id": existing_album["id"]}
        else:
            log.info("    Album already monitored in Lidarr (id=%d)", existing_album["id"])
            requested[spotify_album_id] = {"status": "already_monitored", "lidarr_id": existing_album["id"]}
        save_state(state)
        return

    # ── Step 2: Resolve the artist — use per-run cache to avoid repeat work ─
    artist_key = spotify_artist_name.lower()

    if artist_key not in lidarr._run_artist_cache:
        # Check existing Lidarr library first
        lidarr_artist = lidarr.find_artist_in_library(spotify_artist_name)

        if lidarr_artist is None:
            log.info("    Artist not in Lidarr, looking up: %s", spotify_artist_name)
            artist_info = lidarr.lookup_artist_by_name(spotify_artist_name)
            if artist_info is None:
                log.warning("    No confident match for artist '%s' — skipping", spotify_artist_name)
                lidarr._run_artist_cache[artist_key] = None
            else:
                log.info("    Adding artist to Lidarr: %s", artist_info["artistName"])
                try:
                    lidarr_artist = lidarr.add_artist(artist_info)
                    lidarr._artist_cache = None  # invalidate after add
                    lidarr._run_artist_cache[artist_key] = lidarr_artist
                    # Kick off a refresh so Lidarr populates the album list
                    lidarr.refresh_artist(lidarr_artist["id"])
                except requests.HTTPError as exc:
                    log.error("    Failed to add artist '%s': %s", spotify_artist_name, exc)
                    lidarr._run_artist_cache[artist_key] = None
        else:
            lidarr._run_artist_cache[artist_key] = lidarr_artist

    lidarr_artist = lidarr._run_artist_cache.get(artist_key)
    if lidarr_artist is None:
        requested[spotify_album_id] = {"status": "artist_not_found"}
        save_state(state)
        return

    artist_id = lidarr_artist["id"]

    # ── Step 3: Find the album in the artist's catalogue ───────────────────
    # Give Lidarr a short moment if the artist was just added this run
    album = lidarr.find_album_in_artist(artist_id, spotify_album_name)

    if album is None:
        # One short wait, then give up for this run — next run will retry
        log.info("    Album not visible yet, waiting 8s for Lidarr to index…")
        time.sleep(8)
        album = lidarr.find_album_in_artist(artist_id, spotify_album_name)

    if album is None:
        log.warning(
            "    '%s' not found in Lidarr for artist '%s' — will retry next run",
            spotify_album_name, spotify_artist_name,
        )
        requested[spotify_album_id] = {"status": "album_pending_refresh", "artist_id": artist_id}
        save_state(state)
        return

    # ── Step 4: Monitor + search ────────────────────────────────────────────
    lidarr.monitor_and_search_album(album["id"])
    requested[spotify_album_id] = {"status": "requested", "lidarr_id": album["id"]}
    log.info(
        "    ✓ Album queued: %s – %s (Lidarr id=%d)",
        spotify_artist_name, album.get("title", spotify_album_name), album["id"],
    )
    save_state(state)


def sync_playlist(
    playlist_cfg: dict,
    sp: spotipy.Spotify,
    jf: JellyfinClient,
    lidarr: LidarrClient,
    state: dict,
    cfg: dict,
) -> None:
    spotify_playlist_id: str = playlist_cfg["spotify_playlist_id"]
    jellyfin_playlist_name: str = playlist_cfg.get(
        "jellyfin_playlist_name", f"Spotify – {spotify_playlist_id}"
    )
    sync_mode: str = playlist_cfg.get("sync_mode", "add_only")  # add_only | full_sync

    log.info("═" * 60)
    log.info("Syncing playlist: %s", jellyfin_playlist_name)
    log.info("  Spotify ID : %s", spotify_playlist_id)
    log.info("  Sync mode  : %s", sync_mode)

    # --- Fetch Spotify tracks ---
    sp_tracks = get_playlist_tracks(sp, spotify_playlist_id)
    if not sp_tracks:
        log.warning("  Empty Spotify playlist, skipping.")
        return

    # --- Match against Jellyfin ---
    matched_ids: list[str] = []       # Jellyfin ItemIds ready to add
    missing: list[dict] = []          # Spotify tracks not found locally

    for track in sp_tracks:
        title = track["name"]
        artist = primary_artist(track)
        jf_item = jf.find_track(title, artist)
        if jf_item:
            matched_ids.append(jf_item["Id"])
        else:
            missing.append(track)

    log.info(
        "  Matched: %d / %d   Missing: %d",
        len(matched_ids),
        len(sp_tracks),
        len(missing),
    )

    # --- Update Jellyfin playlist ---
    pl_id = jf.get_or_create_playlist(jellyfin_playlist_name)

    if sync_mode == "full_sync":
        # Remove items no longer in the Spotify playlist
        existing_items = jf.get_playlist_items(pl_id)
        existing_item_ids = {i["Id"] for i in existing_items}
        matched_set = set(matched_ids)
        to_remove_entry_ids = [
            i["PlaylistItemId"]
            for i in existing_items
            if i["Id"] not in matched_set
        ]
        if to_remove_entry_ids:
            log.info("  Removing %d stale items from Jellyfin playlist", len(to_remove_entry_ids))
            jf.remove_from_playlist(pl_id, to_remove_entry_ids)
        # Only add truly new items
        new_ids = [iid for iid in matched_ids if iid not in existing_item_ids]
    else:
        # add_only: never remove, only add what's not already there
        existing_items = jf.get_playlist_items(pl_id)
        existing_item_ids = {i["Id"] for i in existing_items}
        new_ids = [iid for iid in matched_ids if iid not in existing_item_ids]

    if new_ids:
        log.info("  Adding %d new tracks to Jellyfin playlist", len(new_ids))
        # Jellyfin has a URL-length limit; chunk to be safe
        chunk_size = 100
        for i in range(0, len(new_ids), chunk_size):
            jf.add_to_playlist(pl_id, new_ids[i : i + chunk_size])
    else:
        log.info("  Jellyfin playlist already up to date.")

    # --- Send missing albums to Lidarr ---
    if not missing:
        log.info("  No missing tracks — nothing to request in Lidarr.")
        return

    # Deduplicate by album ID so we only request each album once
    seen_albums: set[str] = set()
    for track in missing:
        album = track.get("album", {})
        album_id = album.get("id")
        if not album_id or album_id in seen_albums:
            continue
        seen_albums.add(album_id)
        album_name = album.get("name", "Unknown Album")
        album_artists = album.get("artists", [])
        artist_name = album_artists[0]["name"] if album_artists else primary_artist(track)

        # Re-attempt albums that got stuck on album_pending_refresh in a PRIOR run.
        # Don't clear entries added in this run — those will be retried next time.
        existing_entry = state["lidarr_requested_albums"].get(album_id, {})
        if existing_entry.get("status") == "album_pending_refresh" and \
                existing_entry.get("run") != state.get("current_run"):
            log.info("  Retrying pending album from previous run: %s – %s", artist_name, album_name)
            del state["lidarr_requested_albums"][album_id]

        request_album_in_lidarr(
            lidarr, album_id, album_name, artist_name, state
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = load_config()
    state = load_state()

    # Stamp a run ID so album_pending_refresh entries from this run aren't
    # retried until the next run
    import datetime
    state["current_run"] = datetime.datetime.utcnow().isoformat()

    sp = make_spotify_client(cfg)
    jf = JellyfinClient(cfg)
    lidarr = LidarrClient(cfg)

    playlists: list[dict] = cfg.get("playlists", [])
    if not playlists:
        log.error("No playlists defined in config.json")
        sys.exit(1)

    for pl_cfg in playlists:
        try:
            sync_playlist(pl_cfg, sp, jf, lidarr, state, cfg)
        except Exception as exc:
            log.exception("Error syncing playlist %s: %s", pl_cfg.get("spotify_playlist_id"), exc)

    log.info("═" * 60)
    log.info("Sync complete.")
    save_state(state)


if __name__ == "__main__":
    main()
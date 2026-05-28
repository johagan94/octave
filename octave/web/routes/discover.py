"""Service auto-discovery and credential-acquisition helpers.

GET  /api/discover/services              -- probe LAN for Jellyfin / Lidarr
POST /api/discover/jellyfin/connect      -- username+password -> api_key + user_id
POST /api/discover/jellyfin/libraries    -- api_key + user_id -> media library list
POST /api/discover/lidarr/validate       -- url + api_key -> connectivity check
GET  /api/discover/similar_artists       -- Last.fm similar artists not yet in library
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter, HTTPException

from ..envelope import ok

log = logging.getLogger(__name__)
router = APIRouter(prefix="/discover")

_PROBE_TIMEOUT = httpx.Timeout(3.0, connect=1.5)
_AUTH_TIMEOUT  = httpx.Timeout(10.0, connect=3.0)

# Candidates probed in order during auto-discovery.
# Docker service hostnames are tried first so homelab setups resolve instantly.
_JELLYFIN_CANDIDATES = [
    "http://jellyfin:8096",
    "http://jellyfin:8920",
    "http://localhost:8096",
    "http://127.0.0.1:8096",
]
_LIDARR_CANDIDATES = [
    "http://lidarr:8686",
    "http://localhost:8686",
    "http://127.0.0.1:8686",
]

_JF_AUTH_HEADER = (
    'MediaBrowser Client="Octave", Device="browser", '
    'DeviceId="octave-setup", Version="3.0"'
)


async def _probe(client: httpx.AsyncClient, url: str, path: str) -> bool:
    try:
        r = await client.get(url.rstrip("/") + path)
        return r.status_code < 500
    except Exception:
        return False


@router.get("/services")
async def discover_services():
    """Probe common hostnames for Jellyfin and Lidarr. Returns reachable base URLs."""
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
        jf_results, li_results = await asyncio.gather(
            asyncio.gather(*[_probe(client, u, "/System/Info/Public") for u in _JELLYFIN_CANDIDATES]),
            asyncio.gather(*[_probe(client, u, "/api/v1/system/status") for u in _LIDARR_CANDIDATES]),
        )
    return ok(data={
        "jellyfin": [u for u, hit in zip(_JELLYFIN_CANDIDATES, jf_results) if hit],
        "lidarr":   [u for u, hit in zip(_LIDARR_CANDIDATES,   li_results) if hit],
    })


@router.post("/jellyfin/connect")
async def jellyfin_connect(body: dict):
    """Authenticate against Jellyfin with username + password.

    Returns api_key (session AccessToken), user_id, and a list of media
    libraries so the caller can immediately pick the music library.
    """
    url      = (body.get("url") or "").strip().rstrip("/")
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not url or not username:
        raise HTTPException(status_code=400, detail="url and username are required")

    try:
        async with httpx.AsyncClient(timeout=_AUTH_TIMEOUT) as client:
            r = await client.post(
                url + "/Users/AuthenticateByName",
                json={"Username": username, "Pw": password},
                headers={
                    "X-Emby-Authorization": _JF_AUTH_HEADER,
                    "Content-Type": "application/json",
                },
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Jellyfin: {exc}")

    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Jellyfin returned HTTP {r.status_code}")

    data    = r.json()
    token   = data.get("AccessToken", "")
    user    = data.get("User", {})
    user_id = user.get("Id", "")

    if not token or not user_id:
        raise HTTPException(status_code=502, detail="Jellyfin response missing token or user ID")

    # Fetch libraries in the same call so the UI can present them immediately
    libraries: list[dict] = []
    try:
        auth = _JF_AUTH_HEADER + f', Token="{token}"'
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            lr = await client.get(
                url + f"/Users/{user_id}/Views",
                headers={"X-Emby-Authorization": auth},
            )
        if lr.status_code == 200:
            libraries = [
                {
                    "id":   item["Id"],
                    "name": item["Name"],
                    "type": item.get("CollectionType", "unknown"),
                }
                for item in lr.json().get("Items", [])
            ]
    except Exception as exc:
        log.warning("Could not fetch Jellyfin libraries after connect: %s", exc)

    return ok(data={
        "api_key":      token,
        "user_id":      user_id,
        "display_name": user.get("Name", username),
        "libraries":    libraries,
        "music_libraries": [lib for lib in libraries if lib["type"] == "music"],
    })


@router.get("/similar_artists")
async def similar_artists(seed: str | None = None, limit: int = 20):
    """Return artists similar to your Jellyfin library that you don't have yet.

    seed: optional Jellyfin artist ID to use as the seed; defaults to top-5
          most-played album artists.
    limit: max suggestions to return (default 20).

    Requires LASTFM_API_KEY to be configured in Settings.
    """
    from ...config import load_config
    from ...jellyfin_client import JellyfinClient
    from ...lastfm import LastFMClient
    from ..settings import get_setting

    lastfm_key = get_setting("LASTFM_API_KEY")
    if not lastfm_key:
        raise HTTPException(503, "LASTFM_API_KEY not configured — set it in Settings → Last.fm")

    try:
        cfg = load_config()
        jf = JellyfinClient(cfg)

        all_artists = await asyncio.to_thread(jf.get_all_album_artists)
        artist_names_lower = {a["Name"].lower() for a in all_artists if a.get("Name")}

        # Resolve seed
        if seed:
            seed_artists = [a for a in all_artists if a.get("Id") == seed]
            if not seed_artists:
                raise HTTPException(404, f"Artist {seed!r} not found in Jellyfin")
        else:
            seed_artists = all_artists[:5]  # top 5 by play count

        lfm = LastFMClient(api_key=lastfm_key)

        # Collect similar artists cross-referencing seed list
        candidates: dict[str, dict] = {}

        def _fetch_similar():
            for artist in seed_artists:
                for s in lfm.get_similar_artists(artist["Name"], limit=30):
                    name = (s.get("name") or "").strip()
                    if not name or name.lower() in artist_names_lower:
                        continue
                    key = name.lower()
                    if key in candidates:
                        candidates[key]["frequency"] += 1
                        candidates[key]["match"] = max(
                            candidates[key]["match"], float(s.get("match") or 0)
                        )
                        if artist["Name"] not in candidates[key]["similar_to"]:
                            candidates[key]["similar_to"].append(artist["Name"])
                    else:
                        candidates[key] = {
                            "name": name,
                            "mbid": s.get("mbid", ""),
                            "url": s.get("url", ""),
                            "match": float(s.get("match") or 0),
                            "frequency": 1,
                            "similar_to": [artist["Name"]],
                        }

        await asyncio.to_thread(_fetch_similar)

        ranked = sorted(
            candidates.values(),
            key=lambda x: (-x["frequency"], -x["match"]),
        )[:limit]

        return ok({
            "artists": ranked,
            "seeded_from": [a["Name"] for a in seed_artists],
        })

    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Similar artist discovery failed")
        raise HTTPException(500, str(exc))


@router.post("/lidarr/validate")
async def lidarr_validate(body: dict):
    """Validate a Lidarr URL + API key. Returns version info on success."""
    url     = (body.get("url") or "").strip().rstrip("/")
    api_key = (body.get("api_key") or "").strip()

    if not url or not api_key:
        raise HTTPException(status_code=400, detail="url and api_key are required")

    try:
        async with httpx.AsyncClient(timeout=_AUTH_TIMEOUT) as client:
            r = await client.get(
                url + "/api/v1/system/status",
                headers={"X-Api-Key": api_key},
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Lidarr: {exc}")

    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Lidarr returned HTTP {r.status_code}")

    info = r.json()
    return ok(data={
        "version": info.get("version"),
        "branch":  info.get("branch"),
    })

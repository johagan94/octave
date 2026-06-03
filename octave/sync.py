"""Per-playlist sync orchestration and Lidarr album-request state machine.

Blocks implemented: production stability, cover art, speed (track cache,
persistent index, parallel Lidarr), duplicate detection, waiting_for_lidarr,
ListenBrainz/LastFM enrichment.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import threading
from collections import Counter
from typing import Optional

import requests
import spotipy

from .jellyfin_client import JellyfinClient
from .lidarr_client import LidarrClient
from .musicbrainz import MusicBrainzResolver
from .spotify_client import (
    get_playlist_cover,
    get_playlist_tracks,
    primary_artist,
    primary_artist_id,
)
from .state import _state_lock, save_state

log = logging.getLogger(__name__)

# Max workers for parallel Lidarr album requests
_MAX_LIDARR_WORKERS = 4


def _jellyfin_playlist_state(state: dict) -> dict:
    mapping = state.setdefault("jellyfin_playlists", {})
    return mapping if isinstance(mapping, dict) else {}


def _playlist_name_matches(playlist: dict, name: str) -> bool:
    return playlist.get("Name", "").lower() == name.lower()


def _resolve_jellyfin_playlist_id(
    spotify_id: str,
    jf_name: str,
    jf: JellyfinClient,
    state: dict,
) -> str:
    playlist_state = _jellyfin_playlist_state(state)
    playlists = jf.get_playlists()
    by_id = {pl.get("Id"): pl for pl in playlists if pl.get("Id")}

    mapped_id = playlist_state.get(spotify_id)
    if mapped_id:
        if mapped_id in by_id:
            return mapped_id
        log.warning(
            "  Stored Jellyfin playlist id %s for Spotify playlist %s no longer exists",
            mapped_id, spotify_id,
        )
        playlist_state.pop(spotify_id, None)

    matches = [pl for pl in playlists if _playlist_name_matches(pl, jf_name)]
    if matches:
        if len(matches) > 1:
            log.warning(
                "  Found %d Jellyfin playlists named %r; using id=%s",
                len(matches), jf_name, matches[0].get("Id"),
            )
        playlist_state[spotify_id] = matches[0]["Id"]
        save_state(state)
        return matches[0]["Id"]

    playlist_id = jf.get_or_create_playlist(jf_name)
    playlist_state[spotify_id] = playlist_id
    save_state(state)
    return playlist_id


def _rebuild_jellyfin_playlist(
    spotify_id: str,
    jf_name: str,
    jf: JellyfinClient,
    state: dict,
) -> str:
    playlist_state = _jellyfin_playlist_state(state)
    duplicates = [pl for pl in jf.get_playlists() if _playlist_name_matches(pl, jf_name)]
    for pl in duplicates:
        log.info("  rebuild: deleting existing playlist '%s' id=%s", jf_name, pl["Id"])
        jf.delete_playlist(pl["Id"])

    playlist_id = (
        jf.create_playlist(jf_name)
        if hasattr(jf, "create_playlist")
        else jf.get_or_create_playlist(jf_name)
    )
    playlist_state[spotify_id] = playlist_id
    save_state(state)
    return playlist_id


def request_album_in_lidarr(
    lidarr: Optional[LidarrClient],
    mb: Optional[MusicBrainzResolver],
    spotify_album_id: str,
    spotify_album_name: str,
    spotify_artist_id: str,
    spotify_artist_name: str,
    state: dict,
) -> None:
    """Non-blocking state machine for getting an album monitored in Lidarr.

    Each call does only what it can right now and records where to pick up
    next run. No sleeps, no polling loops.

    States
    ──────
      (none)            → fresh album, start from scratch
      artist_added      → artist was added last run, check if albums appeared
      artist_not_found  → lookup failed, retry next run
      album_pending     → artist has albums but target not found yet, retry
      already_monitored → done
      requested         → done
    """
    with _state_lock:
        requested = state["lidarr_requested_albums"]
        entry = requested.get(spotify_album_id, {})
        status = entry.get("status")

    if status in ("already_monitored", "requested"):
        return

    log.debug("  → [%s] %s – %s", status or "new", spotify_artist_name, spotify_album_name)

    # ── Check if album already exists in Lidarr library ───────────────────
    existing = lidarr.find_album_in_library(
        spotify_album_name, spotify_artist_name, spotify_artist_name
    )
    if existing:
        if not existing.get("monitored"):
            lidarr.monitor_and_search_album(existing["id"])
            with _state_lock:
                requested[spotify_album_id] = {
                    "status": "requested", "lidarr_id": existing["id"],
                }
            log.info("    ✓ Found in library, now monitored (id=%d)", existing["id"])
        else:
            log.info("    Already monitored (id=%d)", existing["id"])
            with _state_lock:
                requested[spotify_album_id] = {
                    "status": "already_monitored", "lidarr_id": existing["id"],
                }
        save_state(state)
        return

    # ── Resolve / add the artist ──────────────────────────────────────────
    # Use a per-artist lock so parallel threads (different albums, same artist)
    # don't race to add the same artist to Lidarr simultaneously.
    artist_key = spotify_artist_name.lower()
    with lidarr._run_artist_lock_guard:
        if artist_key not in lidarr._run_artist_locks:
            lidarr._run_artist_locks[artist_key] = threading.Lock()
        _artist_lock = lidarr._run_artist_locks[artist_key]

    with _artist_lock:
        # Double-checked: re-read cache inside the lock (another thread may
        # have already resolved and cached this artist while we were waiting).
        lidarr_artist: Optional[dict] = lidarr._run_artist_cache.get(artist_key, ...)  # type: ignore[assignment]

        if lidarr_artist is ...:
            # Not yet resolved — look it up / add it now.
            lidarr_artist = lidarr.find_artist_in_library(spotify_artist_name)

            if lidarr_artist is None:
                artist_info = None
                artist_mbid = mb.get_artist_mbid(spotify_artist_id) if spotify_artist_id else None

                if artist_mbid:
                    artist_info = lidarr.lookup_artist_mbid(artist_mbid)

                if artist_info is None:
                    artist_info = lidarr.lookup_artist_by_name(spotify_artist_name)

                if artist_info is None:
                    lidarr._run_artist_cache[artist_key] = None
                    with _state_lock:
                        requested[spotify_album_id] = {
                            "status": "artist_not_found", "run": state["current_run"],
                        }
                    save_state(state)
                    return

                try:
                    lidarr_artist = lidarr.add_artist(artist_info)
                    lidarr._artist_cache = None
                    lidarr.refresh_artist(lidarr_artist["id"])
                    log.info(
                        "    Artist added (id=%d), refresh triggered — "
                        "albums will appear next run", lidarr_artist["id"],
                    )
                except requests.HTTPError as exc:
                    dedup_key = f"add_fail:{spotify_artist_name}"
                    if dedup_key not in lidarr._logged_failures:
                        log.error("    Add artist failed (%s): %s", spotify_artist_name, exc)
                        lidarr._logged_failures.add(dedup_key)
                    else:
                        log.debug("    Add artist failed (%s): %s", spotify_artist_name, exc)
                    lidarr._run_artist_cache[artist_key] = None
                    with _state_lock:
                        requested[spotify_album_id] = {
                            "status": "artist_add_failed", "run": state["current_run"],
                        }
                    save_state(state)
                    return

            lidarr._run_artist_cache[artist_key] = lidarr_artist
        # else: already cached (by this or another thread) — use it as-is

    if lidarr_artist is None:
        if entry.get("run") != state["current_run"]:
            with _state_lock:
                del requested[spotify_album_id]
        save_state(state)
        return

    artist_id = lidarr_artist["id"]

    # ── Try to find the album right now ───────────────────────────────────
    albums = lidarr.get_artist_albums(artist_id)

    if not albums:
        try:
            lidarr.refresh_artist(artist_id)
        except Exception as exc:
            log.warning("    Refresh error: %s", exc)
        with _state_lock:
            requested[spotify_album_id] = {
                "status":    "artist_added",
                "artist_id": artist_id,
                "run":       state["current_run"],
            }
        save_state(state)
        return

    album = lidarr.find_album_in_artist(artist_id, spotify_album_name, albums)

    if album is None:
        try:
            lidarr.refresh_artist(artist_id)
        except Exception:
            pass
        with _state_lock:
            requested[spotify_album_id] = {
                "status":    "album_pending",
                "artist_id": artist_id,
                "run":       state["current_run"],
            }
        save_state(state)
        return

    # ── Monitor + search ──────────────────────────────────────────────────
    lidarr.monitor_and_search_album(album["id"])
    with _state_lock:
        requested[spotify_album_id] = {"status": "requested", "lidarr_id": album["id"]}
    log.info(
        "    ✓ Queued: %s – %s (lidarr_id=%d)",
        spotify_artist_name, album.get("title", spotify_album_name), album["id"],
    )
    save_state(state)


def sync_playlist(
    playlist_cfg: dict,
    sp: spotipy.Spotify,
    jf: JellyfinClient,
    lidarr: LidarrClient,
    mb: MusicBrainzResolver,
    state: dict,
    playlist_num: int,
    playlist_total: int,
    listenbrainz=None,
    lastfm=None,
) -> dict:
    """Sync one playlist; returns ``{matched, missing, albums_requested, waiting_lidarr}``."""
    spotify_id = playlist_cfg["spotify_playlist_id"]
    # Use .get() OR-fallback (not the default arg): an explicit None value
    # in the dict would otherwise pass through as the literal name "None".
    jf_name = playlist_cfg.get("jellyfin_playlist_name") or f"Spotify – {spotify_id}"
    sync_mode = playlist_cfg.get("sync_mode") or "add_only"

    log.info("═" * 60)
    log.info(
        "Playlist %d/%d: %s  [%s]",
        playlist_num, playlist_total, jf_name, sync_mode,
    )

    try:
        sp_tracks = get_playlist_tracks(sp, spotify_id)
    except Exception as exc:
        log.error("  Spotify: failed to fetch tracks for %s: %s", spotify_id, exc)
        raise RuntimeError(f"Spotify failed to fetch playlist {spotify_id}: {exc}") from exc

    if not sp_tracks:
        log.warning("  Empty playlist, skipping.")
        return {"matched": 0, "missing": 0, "albums_requested": 0, "waiting_lidarr": 0}

    # Deduplicate: detect duplicate track IDs within the Spotify playlist
    track_id_counts = Counter(t["id"] for t in sp_tracks if t.get("id"))
    dupes = {tid: c for tid, c in track_id_counts.items() if c > 1}
    if dupes:
        log.warning(
            "  Duplicate tracks in playlist: %d tracks appear multiple times — "
            "only first occurrence synced", len(dupes),
        )
        seen_tids: set[str] = set()
        deduped: list[dict] = []
        for t in sp_tracks:
            tid = t.get("id")
            if tid and tid in seen_tids:
                continue
            if tid:
                seen_tids.add(tid)
            deduped.append(t)
        sp_tracks = deduped

    try:
        jf._build_index()
    except Exception as exc:
        log.error("  Jellyfin: failed to build library index: %s", exc)
        raise RuntimeError(f"Jellyfin failed to build library index: {exc}") from exc

    waiting_track_ids = set(state.get("waiting_for_lidarr_tracks", {}).keys())

    # Promote any waiting tracks that Lidarr has since downloaded into Jellyfin
    if waiting_track_ids:
        resolved: list[str] = []
        for track in sp_tracks:
            tid = track.get("id", "")
            if tid not in waiting_track_ids:
                continue
            if jf.find_track(track["name"], primary_artist(track), tid):
                resolved.append(tid)
        if resolved:
            with _state_lock:
                for tid in resolved:
                    state["waiting_for_lidarr_tracks"].pop(tid, None)
            waiting_track_ids -= set(resolved)
            log.info(
                "  %d waiting track(s) now in Jellyfin library; removed from wait list",
                len(resolved),
            )

    # ── Match against Jellyfin ────────────────────────────────────────────
    matched_ids: list[str] = []
    missing: list[dict] = []
    waiting_lidarr: list[str] = []

    for track in sp_tracks:
        try:
            title = track["name"]
            artist = primary_artist(track)
            spotify_track_id = track.get("id", "")

            # Check if this track is still waiting for Lidarr to download
            if spotify_track_id in waiting_track_ids:
                waiting_lidarr.append(spotify_track_id)
                continue

            jf_item = jf.find_track(title, artist, spotify_track_id)
            if jf_item:
                matched_ids.append(jf_item["Id"])
            else:
                missing.append(track)
        except Exception as exc:
            log.debug("  Error matching track '%s': %s", track.get("name", "?"), exc)
            missing.append(track)

    log.info(
        "  Matched %d / %d   Missing %d   Waiting Lidarr %d",
        len(matched_ids), len(sp_tracks), len(missing), len(waiting_lidarr),
    )

    # Deduplicate: multiple Spotify tracks can map to the same Jellyfin item
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

    # ── Update Jellyfin playlist ──────────────────────────────────────────
    try:
        if sync_mode == "rebuild":
            pl_id = _rebuild_jellyfin_playlist(spotify_id, jf_name, jf, state)
            existing_item_ids: set[str] = set()
            existing_items: list[dict] = []
        else:
            pl_id = _resolve_jellyfin_playlist_id(spotify_id, jf_name, jf, state)
            existing_items = jf.get_playlist_items(pl_id)
            existing_item_ids = {i["Id"] for i in existing_items}

        if sync_mode == "full_sync":
            matched_set = set(matched_ids)
            to_remove = [
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

        # ── Cover art ─────────────────────────────────────────────────────
        try:
            cover_bytes = get_playlist_cover(sp, spotify_id)
            if cover_bytes:
                jf.set_playlist_image(pl_id, cover_bytes)
                log.debug("  Cover art updated")
        except Exception as exc:
            log.debug("  Cover art skipped: %s", exc)
    except Exception as exc:
        log.error("  Jellyfin: failed to update playlist: %s", exc)
        raise RuntimeError(f"Jellyfin failed to update playlist {jf_name}: {exc}") from exc

    # ── Send missing albums to Lidarr (parallel) ──────────────────────────
    matched_count = len(matched_ids)
    missing_count = len(missing)

    # Persist missing tracks for UI download
    if missing:
        try:
            write_missing_tracks(jf_name, spotify_id, missing)
        except Exception as exc:
            log.debug("Failed to write missing tracks: %s", exc)

    if not missing:
        return {
            "matched": matched_count, "missing": missing_count,
            "albums_requested": 0, "waiting_lidarr": len(waiting_lidarr),
        }

    if lidarr is None or mb is None:
        log.info("  Lidarr not configured; missing albums were recorded but not requested")
        return {
            "matched": matched_count,
            "missing": missing_count,
            "albums_requested": 0,
            "waiting_lidarr": len(waiting_lidarr),
        }

    seen_albums: set[str] = set()
    album_requests: list[dict] = []
    current_run = state.get("current_run", "")

    for track in missing:
        album = track.get("album", {})

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

        album_name = album.get("name", "Unknown Album")
        album_artists = album.get("artists", [])
        artist_name = album_artists[0]["name"] if album_artists else primary_artist(track)
        artist_id = album_artists[0].get("id", "") if album_artists else primary_artist_id(track)

        # Clear retryable states from previous runs
        existing = state["lidarr_requested_albums"].get(album_id, {})
        if (
            existing.get("status") in (
                "artist_not_found", "artist_add_failed",
                "artist_added", "album_pending",
            )
            and existing.get("run") != current_run
        ):
            log.info(
                "  Retrying [%s] from prev run: %s – %s",
                existing["status"], artist_name, album_name,
            )
            del state["lidarr_requested_albums"][album_id]

        album_requests.append({
            "lidarr": lidarr, "mb": mb,
            "spotify_album_id": album_id, "spotify_album_name": album_name,
            "spotify_artist_id": artist_id, "spotify_artist_name": artist_name,
            "state": state,
        })

    # Parallel Lidarr album requests
    if album_requests:
        with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_LIDARR_WORKERS) as pool:
            futures = [
                pool.submit(
                    request_album_in_lidarr,
                    lidarr=req["lidarr"],
                    mb=req["mb"],
                    spotify_album_id=req["spotify_album_id"],
                    spotify_album_name=req["spotify_album_name"],
                    spotify_artist_id=req["spotify_artist_id"],
                    spotify_artist_name=req["spotify_artist_name"],
                    state=req["state"],
                )
                for req in album_requests
            ]
            for f in concurrent.futures.as_completed(futures):
                try:
                    f.result()
                except Exception as exc:
                    log.error("  Lidarr request thread failed: %s", exc)

    # Track waiting_for_lidarr: mark missing tracks whose albums are now queued
    with _state_lock:
        if "waiting_for_lidarr_tracks" not in state:
            state["waiting_for_lidarr_tracks"] = {}
        for track in missing:
            album_id = track.get("album", {}).get("id")
            if album_id:
                status = state["lidarr_requested_albums"].get(album_id, {}).get("status")
                if status in ("requested", "already_monitored"):
                    spotify_track_id = track.get("id", "")
                    if spotify_track_id:
                        state["waiting_for_lidarr_tracks"][spotify_track_id] = {
                            "album_id": album_id,
                            "status": status,
                            "run": current_run,
                        }

    save_state(state)
    return {
        "matched": matched_count,
        "missing": missing_count,
        "albums_requested": len(seen_albums),
        "waiting_lidarr": len(waiting_lidarr),
    }


def write_missing_tracks(
    playlist_name: str,
    spotify_id: str,
    missing: list[dict],
    output_dir: Optional[str] = None,
) -> None:
    """Persist missing tracks to JSON for UI download."""
    import json
    from pathlib import Path
    if output_dir is None:
        output_dir = os.environ.get("SYNC_DATA_DIR", "data")
    out = Path(output_dir) / "missing_tracks.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if out.exists():
        try:
            existing = json.loads(out.read_text())
        except json.JSONDecodeError:
            pass
    existing[spotify_id] = {
        "playlist_name": playlist_name,
        "tracks": [
            {
                "spotify_id": t.get("id", ""),
                "title": t.get("name", ""),
                "artist": ", ".join(a.get("name", "") for a in t.get("artists", [])),
                "album": t.get("album", {}).get("name", ""),
                "album_type": t.get("album", {}).get("album_type", ""),
                "spotify_url": f"https://open.spotify.com/track/{t.get('id', '')}" if t.get("id") else "",
            }
            for t in missing
        ],
    }
    out.write_text(json.dumps(existing, indent=2))

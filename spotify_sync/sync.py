"""Per-playlist sync orchestration and Lidarr album-request state machine."""

import logging
from typing import Optional

import requests
import spotipy

from .jellyfin_client import JellyfinClient
from .lidarr_client import LidarrClient
from .musicbrainz import MusicBrainzResolver
from .spotify_client import (
    get_playlist_tracks,
    primary_artist,
    primary_artist_id,
)
from .state import save_state

log = logging.getLogger(__name__)


def request_album_in_lidarr(
    lidarr: LidarrClient,
    mb: MusicBrainzResolver,
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
    requested = state["lidarr_requested_albums"]
    entry = requested.get(spotify_album_id, {})
    status = entry.get("status")

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
            requested[spotify_album_id] = {
                "status": "already_monitored", "lidarr_id": existing["id"],
            }
        save_state(state)
        return

    # ── Resolve / add the artist ──────────────────────────────────────────
    artist_key = spotify_artist_name.lower()
    lidarr_artist: Optional[dict] = lidarr._run_artist_cache.get(artist_key, ...)  # type: ignore[assignment]

    if lidarr_artist is ...:
        lidarr_artist = lidarr.find_artist_in_library(spotify_artist_name)

        if lidarr_artist is None:
            artist_info = None
            artist_mbid = mb.get_artist_mbid(spotify_artist_id) if spotify_artist_id else None

            if artist_mbid:
                log.info("    MB MBID resolved: %s", artist_mbid)
                artist_info = lidarr.lookup_artist_mbid(artist_mbid)

            if artist_info is None:
                log.info("    Trying text search: %s", spotify_artist_name)
                artist_info = lidarr.lookup_artist_by_name(spotify_artist_name)

            if artist_info is None:
                log.warning("    Artist not found — will retry next run")
                lidarr._run_artist_cache[artist_key] = None
                requested[spotify_album_id] = {
                    "status": "artist_not_found", "run": state["current_run"],
                }
                save_state(state)
                return

            log.info("    Adding artist: %s", artist_info["artistName"])
            try:
                lidarr_artist = lidarr.add_artist(artist_info)
                lidarr._artist_cache = None
                lidarr.refresh_artist(lidarr_artist["id"])
                log.info(
                    "    Artist added (id=%d), refresh triggered — "
                    "albums will appear next run", lidarr_artist["id"],
                )
            except requests.HTTPError as exc:
                log.error("    Add artist failed: %s", exc)
                lidarr._run_artist_cache[artist_key] = None
                requested[spotify_album_id] = {
                    "status": "artist_add_failed", "run": state["current_run"],
                }
                save_state(state)
                return

        lidarr._run_artist_cache[artist_key] = lidarr_artist

    if lidarr_artist is None:
        if entry.get("run") != state["current_run"]:
            del requested[spotify_album_id]
        save_state(state)
        return

    artist_id = lidarr_artist["id"]

    # ── Try to find the album right now ───────────────────────────────────
    albums = lidarr.get_artist_albums(artist_id)

    if not albums:
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

    # ── Monitor + search ──────────────────────────────────────────────────
    lidarr.monitor_and_search_album(album["id"])
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
) -> dict:
    """Sync one playlist; returns ``{matched, missing, albums_requested}``.

    Returning a stats dict (was ``None``) is backward-compatible:
    ``__main__.main`` previously discarded the return value.
    """
    spotify_id = playlist_cfg["spotify_playlist_id"]
    jf_name = playlist_cfg.get("jellyfin_playlist_name", f"Spotify – {spotify_id}")
    sync_mode = playlist_cfg.get("sync_mode", "add_only")

    log.info("═" * 60)
    log.info(
        "Playlist %d/%d: %s  [%s]",
        playlist_num, playlist_total, jf_name, sync_mode,
    )

    sp_tracks = get_playlist_tracks(sp, spotify_id)
    if not sp_tracks:
        log.warning("  Empty playlist, skipping.")
        return {"matched": 0, "missing": 0, "albums_requested": 0}

    jf._build_index()

    # ── Match against Jellyfin ────────────────────────────────────────────
    matched_ids: list[str] = []
    missing: list[dict] = []

    for track in sp_tracks:
        title = track["name"]
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
    if sync_mode == "rebuild":
        # Wipe any existing playlist with this name and recreate fresh.
        # Guarantees track order matches Spotify exactly and resets any
        # manual edits / stale items that have accumulated in Jellyfin.
        for pl in jf.get_playlists():
            if pl["Name"].lower() == jf_name.lower():
                log.info("  rebuild: deleting existing playlist '%s'", jf_name)
                jf.delete_playlist(pl["Id"])
                break
        pl_id = jf.get_or_create_playlist(jf_name)
        existing_item_ids: set[str] = set()
        existing_items: list[dict] = []
    else:
        pl_id = jf.get_or_create_playlist(jf_name)
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

    # ── Send missing albums to Lidarr ─────────────────────────────────────
    matched_count = len(matched_ids)
    missing_count = len(missing)
    if not missing:
        return {"matched": matched_count, "missing": 0, "albums_requested": 0}

    seen_albums: set[str] = set()
    current_run = state.get("current_run", "")

    for track in missing:
        album = track.get("album", {})

        # Only request full albums (not singles or EPs from Spotify's view)
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

        # Clear retryable states from previous runs so they get re-attempted
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

        request_album_in_lidarr(
            lidarr, mb,
            album_id, album_name,
            artist_id, artist_name,
            state,
        )

    return {
        "matched": matched_count,
        "missing": missing_count,
        "albums_requested": len(seen_albums),
    }

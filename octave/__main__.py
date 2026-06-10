"""Entry point for ``python -m octave`` and the importable
``run_sync()`` used by the FastAPI runner."""

from __future__ import annotations

import datetime
import json
import logging
import sys
from typing import Callable, Optional

import os

from .config import load_config
from .jellyfin_client import JellyfinClient
from .lastfm import LastFMClient
from .lidarr_client import LidarrClient
from .listenbrainz import ListenBrainzClient
from .logging_setup import configure_logging
from .musicbrainz import MusicBrainzResolver
from .spotify_client import get_user_playlists, make_spotify_client
from .state import load_state, save_state
from .sync import sync_playlist
from .track_cache import TrackCache

log = logging.getLogger(__name__)

ProgressCb = Callable[[int, int], None]



def _spotify_call(fn, *args, **kwargs):
    """Call a Spotify API function, refreshing the token once on 401."""
    import spotipy
    try:
        return fn(*args, **kwargs)
    except spotipy.SpotifyException as exc:
        if exc.http_status != 401:
            raise
        log.warning("Spotify token expired mid-sync — refreshing and retrying")
        from .spotify_auth import refresh_access_token
        new_token = refresh_access_token()
        if not new_token:
            raise RuntimeError("Spotify token refresh failed; re-connect in Settings") from exc
        import spotipy as _sp
        # Re-wrap the client the sync loop already holds — patch its auth
        kwargs.get("sp", None)  # no-op; caller must rebuild sp for next call
        raise  # let run_sync rebuild sp on next iteration via make_spotify_client

def run_sync(
    progress_cb: Optional[ProgressCb] = None,
    playlist_ids: Optional[list[str]] = None,
) -> dict:
    """Run the sync pipeline once and return aggregate stats.

    ``playlist_ids`` — if provided, only sync playlists whose
    ``spotify_playlist_id`` is in the list. ``None`` means sync all.
    """
    cfg = load_config()
    state = load_state()
    state["current_run"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Load track cache for faster matching across runs
    track_cache = TrackCache()
    track_cache.load()

    sp = make_spotify_client(cfg)
    jf = JellyfinClient(cfg, track_cache=track_cache)
    lidarr_cfg = cfg.get("lidarr", {})
    lidarr_enabled = bool(
        str(lidarr_cfg.get("url", "")).strip()
        and str(lidarr_cfg.get("api_key", "")).strip()
    )
    lidarr = LidarrClient(cfg) if lidarr_enabled else None
    mb = MusicBrainzResolver() if lidarr_enabled else None
    if not lidarr_enabled:
        log.info("Lidarr is not configured; missing-track requests will be skipped")

    # Validate track cache against current Jellyfin library. If tracks are
    # waiting on Lidarr downloads, force a live rebuild (bypass the <1h disk
    # cache) so files Lidarr has since imported are visible and can be matched
    # into playlists this run instead of staying stuck.
    force_index = bool(state.get("waiting_for_lidarr_tracks"))
    jf._build_index(force_reload=force_index)
    valid_ids = {item["Id"] for item in (jf._library_cache or [])}
    track_cache.validate(valid_ids)

    from .web.settings import get_setting
    sync_all = get_setting("SYNC_ALL_PLAYLISTS", "").strip().lower() in ("1", "true", "yes", "on")
    listenbrainz_token = get_setting("LISTENBRAINZ_TOKEN", "").strip()
    lastfm_api_key = get_setting("LASTFM_API_KEY", "").strip()
    lb = ListenBrainzClient(listenbrainz_token) if listenbrainz_token else None
    lfm = LastFMClient(lastfm_api_key) if lastfm_api_key else None

    if sync_all:
        log.info("SYNC_ALL_PLAYLISTS enabled — discovering all account playlists")
        all_playlists = get_user_playlists(sp)
        try:
            cache_dir = os.environ.get("SYNC_DATA_DIR", "data")
            os.makedirs(cache_dir, exist_ok=True)
            with open(os.path.join(cache_dir, "discovered_playlists.json"), "w") as fh:
                json.dump(all_playlists, fh, indent=2)
        except Exception:
            log.exception("Could not cache discovered Spotify playlists")
        if not all_playlists:
            log.error("SYNC_ALL_PLAYLISTS is on but no playlists were discovered "
                      "(is Spotify connected via PKCE?)")
            raise RuntimeError(
                "SYNC_ALL_PLAYLISTS is enabled but no Spotify playlists were found. "
                "Connect Spotify in Settings."
            )
    else:
        all_playlists = cfg.get("playlists", [])
        if not all_playlists:
            log.error("No playlists defined in config.json")
            raise RuntimeError("No playlists defined in config.json")

    playlists = (
        [p for p in all_playlists if p.get("spotify_playlist_id") in playlist_ids]
        if playlist_ids is not None
        else all_playlists
    )
    if not playlists:
        raise RuntimeError(f"No matching playlists found for ids: {playlist_ids}")

    totals = {
        "matched": 0, "missing": 0, "albums_requested": 0,
        "playlists": 0, "waiting_lidarr": 0,
    }
    total = len(playlists)
    errors: list[str] = []
    for n, pl_cfg in enumerate(playlists, 1):
        playlist_id = pl_cfg.get("spotify_playlist_id", f"playlist-{n}")
        try:
            try:
                stats = sync_playlist(pl_cfg, sp, jf, lidarr, mb, state, n, total, lb, lfm)
            except Exception as _exc:
                # Retry once on Spotify token expiry
                import spotipy as _spy
                if isinstance(_exc, _spy.SpotifyException) and _exc.http_status == 401:
                    log.warning("Spotify 401 on playlist %s — refreshing token and retrying", playlist_id)
                    from .spotify_auth import refresh_access_token
                    if refresh_access_token():
                        sp = make_spotify_client(cfg)
                        stats = sync_playlist(pl_cfg, sp, jf, lidarr, mb, state, n, total, lb, lfm)
                    else:
                        raise RuntimeError("Spotify token refresh failed; re-connect in Settings") from _exc
                else:
                    raise
            totals["matched"] += stats.get("matched", 0)
            totals["missing"] += stats.get("missing", 0)
            totals["albums_requested"] += stats.get("albums_requested", 0)
            totals["waiting_lidarr"] += stats.get("waiting_lidarr", 0)
            totals["playlists"] += 1
        except Exception as exc:
            log.exception(
                "Error syncing playlist %s: %s",
                playlist_id, exc,
            )
            errors.append(f"{playlist_id}: {exc}")
        finally:
            if progress_cb:
                try:
                    progress_cb(n, total)
                except Exception:
                    log.exception("progress_cb raised; ignoring")

    # Save track cache for next run
    cache_stats = jf.get_cache_stats()
    log.info(
        "Track cache: %d entries, %d hits, %d misses this run",
        len(track_cache), cache_stats["hits"], cache_stats["misses"],
    )
    track_cache.save()

    log.info("═" * 60)
    log.info(
        "Sync complete. playlists=%d matched=%d missing=%d "
        "albums_requested=%d waiting_lidarr=%d",
        totals["playlists"], totals["matched"],
        totals["missing"], totals["albums_requested"],
        totals["waiting_lidarr"],
    )
    save_state(state)
    if errors:
        preview = "; ".join(errors[:3])
        more = f" (+{len(errors) - 3} more)" if len(errors) > 3 else ""
        raise RuntimeError(
            f"Sync failed for {len(errors)} of {total} playlist(s): {preview}{more}"
        )
    if totals["playlists"] == 0:
        raise RuntimeError("No playlists synced successfully")
    return totals


def main() -> None:
    configure_logging()
    try:
        run_sync()
    except (RuntimeError, Exception) as exc:  # noqa: BLE001 — top-level CLI handler
        log.error("%s: %s", type(exc).__name__, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

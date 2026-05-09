"""Entry point for ``python -m spotify_sync`` and the importable
``run_sync()`` used by the FastAPI runner."""

from __future__ import annotations

import datetime
import logging
import sys
from typing import Callable, Optional

from .config import load_config
from .jellyfin_client import JellyfinClient
from .lidarr_client import LidarrClient
from .logging_setup import configure_logging
from .musicbrainz import MusicBrainzResolver
from .spotify_client import make_spotify_client
from .state import load_state, save_state
from .sync import sync_playlist

log = logging.getLogger(__name__)

ProgressCb = Callable[[int, int], None]


def run_sync(progress_cb: Optional[ProgressCb] = None) -> dict:
    """Run the full sync pipeline once and return aggregate stats.

    Caller is responsible for configuring logging (FastAPI does this once
    at startup; ``main()`` does it for CLI invocation). ``progress_cb``,
    if provided, is invoked as ``progress_cb(playlist_num, playlist_total)``
    after each playlist completes — the runner uses this to update SyncRun.
    """
    cfg = load_config()
    state = load_state()
    state["current_run"] = datetime.datetime.utcnow().isoformat()

    sp = make_spotify_client(cfg)
    jf = JellyfinClient(cfg)
    lidarr = LidarrClient(cfg)
    mb = MusicBrainzResolver()

    playlists = cfg.get("playlists", [])
    if not playlists:
        log.error("No playlists defined in config.json")
        raise RuntimeError("No playlists defined in config.json")

    totals = {"matched": 0, "missing": 0, "albums_requested": 0, "playlists": 0}
    total = len(playlists)
    for n, pl_cfg in enumerate(playlists, 1):
        try:
            stats = sync_playlist(pl_cfg, sp, jf, lidarr, mb, state, n, total)
            if stats:
                totals["matched"] += stats.get("matched", 0)
                totals["missing"] += stats.get("missing", 0)
                totals["albums_requested"] += stats.get("albums_requested", 0)
                totals["playlists"] += 1
        except Exception as exc:
            log.exception(
                "Error syncing playlist %s: %s",
                pl_cfg.get("spotify_playlist_id"), exc,
            )
        finally:
            if progress_cb:
                try:
                    progress_cb(n, total)
                except Exception:
                    log.exception("progress_cb raised; ignoring")

    log.info("═" * 60)
    log.info(
        "Sync complete. playlists=%d matched=%d missing=%d albums_requested=%d",
        totals["playlists"], totals["matched"],
        totals["missing"], totals["albums_requested"],
    )
    save_state(state)
    return totals


def main() -> None:
    configure_logging()
    try:
        run_sync()
    except (RuntimeError, Exception) as exc:  # noqa: BLE001 — top-level CLI handler
        # ConfigError (subclass of RuntimeError) covers missing env / bad config.
        # Anything else is a genuine bug; both should produce a non-zero exit.
        log.error("%s: %s", type(exc).__name__, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

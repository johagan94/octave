"""Pod 1 verification: import every module without performing any I/O."""

import importlib
import sys

modules = [
    "spotify_sync",
    "spotify_sync.config",
    "spotify_sync.state",
    "spotify_sync.http_utils",
    "spotify_sync.logging_setup",
    "spotify_sync.matcher",
    "spotify_sync.spotify_client",
    "spotify_sync.jellyfin_client",
    "spotify_sync.lidarr_client",
    "spotify_sync.musicbrainz",
    "spotify_sync.sync",
    "spotify_sync.__main__",
]

for m in modules:
    try:
        mod = importlib.import_module(m)
    except Exception as exc:
        print(f"FAIL  {m}: {exc!r}")
        sys.exit(1)
    print(f"ok    {m}")

import spotify_sync
print(f"\npackage version: {spotify_sync.__version__}")

# Spot-check a few public symbols are reachable
from spotify_sync.matcher import normalise, score_pair, best_match  # noqa: F401
from spotify_sync.jellyfin_client import JellyfinClient  # noqa: F401
from spotify_sync.lidarr_client import LidarrClient  # noqa: F401
from spotify_sync.musicbrainz import MusicBrainzResolver  # noqa: F401
from spotify_sync.spotify_client import (  # noqa: F401
    make_spotify_client, get_playlist_tracks, primary_artist,
)
from spotify_sync.sync import sync_playlist, request_album_in_lidarr  # noqa: F401
from spotify_sync.__main__ import main  # noqa: F401
print("public symbols reachable: OK")

# Smoke-test pure functions (no I/O)
assert normalise("The Beatles (feat. John)") == "beatles"
assert normalise("Dénouement") == "denouement"
score, strategy = score_pair("Bohemian Rhapsody", "bohemian rhapsody")
assert score == 100.0, f"expected 100, got {score}"
print("pure-function smoke test: OK")

"""Pod 1 verification: import every module without performing any I/O."""

import importlib
import sys

modules = [
    "octave",
    "octave.config",
    "octave.state",
    "octave.http_utils",
    "octave.logging_setup",
    "octave.matcher",
    "octave.spotify_client",
    "octave.jellyfin_client",
    "octave.lidarr_client",
    "octave.musicbrainz",
    "octave.sync",
    "octave.__main__",
]

for m in modules:
    try:
        mod = importlib.import_module(m)
    except Exception as exc:
        print(f"FAIL  {m}: {exc!r}")
        sys.exit(1)
    print(f"ok    {m}")

import octave
print(f"\npackage version: {octave.__version__}")

# Spot-check a few public symbols are reachable
from octave.matcher import normalise, score_pair, best_match  # noqa: F401
from octave.jellyfin_client import JellyfinClient  # noqa: F401
from octave.lidarr_client import LidarrClient  # noqa: F401
from octave.musicbrainz import MusicBrainzResolver  # noqa: F401
from octave.spotify_client import (  # noqa: F401
    make_spotify_client, get_playlist_tracks, primary_artist,
)
from octave.sync import sync_playlist, request_album_in_lidarr  # noqa: F401
from octave.__main__ import main  # noqa: F401
print("public symbols reachable: OK")

# Smoke-test pure functions (no I/O)
assert normalise("The Beatles (feat. John)") == "beatles"
assert normalise("Dénouement") == "denouement"
score, strategy = score_pair("Bohemian Rhapsody", "bohemian rhapsody")
assert score == 100.0, f"expected 100, got {score}"
print("pure-function smoke test: OK")

"""Persistent Spotify → Jellyfin track ID cache.

Avoids re-searching 2500 library items per track per sync. Cached IDs
are validated on load (stale entries removed silently).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_CACHE_PATH = Path("data/jellyfin_track_cache.json")


class TrackCache:
    """Bidirectional cache: spotify_id → jellyfin_id, with inverse for O(1)."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or DEFAULT_CACHE_PATH
        self._forward: dict[str, str] = {}   # spotify_id → jellyfin_id
        self._reverse: dict[str, str] = {}   # jellyfin_id → spotify_id
        self._dirty = False

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open() as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Track cache corrupt; rebuilding: %s", exc)
            return
        forward = data.get("forward", {})
        self._forward = forward
        self._reverse = {v: k for k, v in forward.items()}
        self._dirty = False
        log.info("Track cache loaded: %d entries", len(self._forward))

    def save(self) -> None:
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("w") as fh:
            json.dump({"forward": self._forward}, fh)
        tmp.replace(self._path)
        self._dirty = False
        log.debug("Track cache saved: %d entries", len(self._forward))

    def get(self, spotify_id: str) -> Optional[str]:
        return self._forward.get(spotify_id)

    def get_reverse(self, jellyfin_id: str) -> Optional[str]:
        return self._reverse.get(jellyfin_id)

    def set(self, spotify_id: str, jellyfin_id: str) -> None:
        old_jf = self._forward.get(spotify_id)
        if old_jf == jellyfin_id:
            return
        if old_jf:
            self._reverse.pop(old_jf, None)
        self._forward[spotify_id] = jellyfin_id
        self._reverse[jellyfin_id] = spotify_id
        self._dirty = True

    def remove(self, spotify_id: str) -> None:
        jf_id = self._forward.pop(spotify_id, None)
        if jf_id:
            self._reverse.pop(jf_id, None)
            self._dirty = True

    def validate(self, valid_jellyfin_ids: set[str]) -> None:
        """Remove cached mappings whose Jellyfin IDs no longer exist."""
        stale = [
            sp_id for sp_id, jf_id in self._forward.items()
            if jf_id not in valid_jellyfin_ids
        ]
        if stale:
            for sp_id in stale:
                self.remove(sp_id)
            log.info("Track cache: removed %d stale entries", len(stale))

    def __len__(self) -> int:
        return len(self._forward)

"""Persistent settings store.

Reads/writes ``settings.json`` in the data directory.  Values here act as a
fallback for environment variables so that credentials can be configured
entirely from the UI without editing ``.env``.

Thread-safe via a file lock (atomic read/write).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# All secret/credential keys that can be managed via the UI.
SECRET_KEYS = [
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "SPOTIFY_REDIRECT_URI",
    "JELLYFIN_URL",
    "JELLYFIN_API_KEY",
    "JELLYFIN_USER_ID",
    "LIDARR_URL",
    "LIDARR_API_KEY",
    "LISTENBRAINZ_TOKEN",
    "LASTFM_API_KEY",
    "AUTH_USERNAME",
    "AUTH_PASSWORD",
]

# Non-secret runtime knobs that can also be tweaked from the UI.
KNOB_KEYS = [
    "SYNC_ON_STARTUP",
    "SYNC_ALL_PLAYLISTS",
    "SYNC_SCHEDULE",
    "LOG_LEVEL",
    "TZ",
    "WEB_PORT",
]

ALL_KEYS = SECRET_KEYS + KNOB_KEYS

_lock = threading.Lock()


def _settings_path() -> Path:
    return Path(os.environ.get("SYNC_DATA_DIR", "/app/data")) / "settings.json"


def _read_raw() -> dict:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        with path.open() as fh:
            return json.load(fh)
    except Exception as exc:
        log.warning("failed to read settings %s: %s", path, exc)
        return {}


def get_setting(key: str, default: str = "") -> str:
    """Return a setting value.  Env var takes priority, then settings.json,
    then the provided default."""
    env_val = os.environ.get(key, "").strip()
    if env_val:
        return env_val
    raw = _read_raw()
    return raw.get(key, default)


def get_all_settings() -> dict:
    """Return all managed settings with env vars taking priority.
    Secrets are masked in the returned dict."""
    raw = _read_raw()
    result = {}
    for key in SECRET_KEYS:
        env_val = os.environ.get(key, "").strip()
        stored = raw.get(key, "")
        effective = env_val or stored
        result[key] = {
            "value": effective,
            "masked": _mask(effective) if effective else "",
            "source": "env" if env_val else ("file" if stored else "unset"),
        }
    for key in KNOB_KEYS:
        env_val = os.environ.get(key, "").strip()
        stored = raw.get(key, "")
        effective = env_val or stored
        result[key] = {
            "value": effective,
            "source": "env" if env_val else ("file" if stored else "unset"),
        }
    return result


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "****"
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def save_settings(updates: dict) -> dict:
    """Persist a dict of key->value pairs to settings.json.

    Only accepts keys in ALL_KEYS.  Returns the full merged settings dict.
    """
    with _lock:
        raw = _read_raw()
        for key, value in updates.items():
            if key not in ALL_KEYS:
                log.warning("ignoring unknown settings key: %s", key)
                continue
            if value is None or (isinstance(value, str) and not value.strip()):
                raw.pop(key, None)
            else:
                raw[key] = str(value).strip()

        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w") as fh:
            json.dump(raw, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # best effort (e.g. Windows / odd filesystems)
        tmp.replace(path)
        log.info("settings saved to %s", path)

        # Also update os.environ for in-process hot-reload of AUTH credentials
        # and other creds that are read on every request.
        for key, value in raw.items():
            if key in ALL_KEYS and not os.environ.get(key, "").strip():
                os.environ[key] = value

        return raw


def has_any_credentials() -> bool:
    """Return True if at least one credential is configured (env or file)."""
    for key in SECRET_KEYS:
        if get_setting(key):
            return True
    return False

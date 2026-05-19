"""Config loading. Non-secret settings come from config.json; credentials
come from environment variables (loaded from .env if present) with a
fallback to the persistent settings.json store managed by the web UI.
"""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

# Load .env once at import time so os.environ is populated before
# anything else reads it. Safe to call multiple times.
load_dotenv()


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded (missing env, bad file)."""


def _get_credential(key: str) -> str:
    """Return a credential value.  Checks os.environ first, then falls back
    to the web-managed settings.json store.  Raises ConfigError if neither
    has a value."""
    env_val = os.environ.get(key, "").strip()
    if env_val:
        return env_val

    # Fallback to settings.json (managed by the UI settings API)
    try:
        from .web.settings import get_setting
        stored = get_setting(key)
        if stored:
            return stored
    except Exception:
        pass

    msg = (
        f"Missing required credential: {key}. "
        f"Add it to your .env file or configure it in the UI Settings page."
    )
    log.error(msg)
    raise ConfigError(msg)


def _get_optional_credential(key: str) -> str:
    """Return a credential value or empty string if not configured."""
    env_val = os.environ.get(key, "").strip()
    if env_val:
        return env_val
    try:
        from .web.settings import get_setting
        return get_setting(key) or ""
    except Exception:
        return ""


def config_path() -> Path:
    """Resolve the active config.json path (env override or cwd default)."""
    return Path(os.environ.get("SYNC_CONFIG", "config.json"))


def load_config() -> dict:
    """Load config.json and inject credentials from the environment or
    the persistent settings store."""
    path = config_path()
    if not path.exists():
        msg = f"Config file not found: {path}"
        log.error(msg)
        raise ConfigError(msg)
    with path.open() as fh:
        try:
            cfg = json.load(fh)
        except json.JSONDecodeError as exc:
            msg = (
                f"Config file {path} has invalid JSON at "
                f"line {exc.lineno}, column {exc.colno}: {exc.msg}. "
                f"Check for trailing commas, trailing content, or "
                f"unclosed brackets."
            )
            log.error(msg)
            raise ConfigError(msg) from exc

    cfg.setdefault("spotify", {})
    from .spotify_auth import DEFAULT_REDIRECT_URI, resolve_client_id
    client_id = resolve_client_id(_get_optional_credential("SPOTIFY_CLIENT_ID"))
    if not client_id:
        raise ConfigError(
            "Missing Spotify Client ID. Configure it in the UI Settings page, "
            "or ship a bundled OCTAVE_BUNDLED_SPOTIFY_CLIENT_ID so end users "
            "need no developer account."
        )
    cfg["spotify"]["client_id"] = client_id
    cfg["spotify"]["client_secret"] = _get_optional_credential("SPOTIFY_CLIENT_SECRET")
    cfg["spotify"]["redirect_uri"] = (
        _get_optional_credential("SPOTIFY_REDIRECT_URI") or DEFAULT_REDIRECT_URI
    )

    cfg.setdefault("jellyfin", {})
    cfg["jellyfin"]["api_key"] = _get_credential("JELLYFIN_API_KEY")
    cfg["jellyfin"]["user_id"] = _get_credential("JELLYFIN_USER_ID")
    jf_url = os.environ.get("JELLYFIN_URL", "").strip()
    if not jf_url:
        try:
            from .web.settings import get_setting
            jf_url = get_setting("JELLYFIN_URL")
        except Exception:
            pass
    if jf_url:
        cfg["jellyfin"]["url"] = jf_url

    cfg.setdefault("lidarr", {})
    cfg["lidarr"]["api_key"] = _get_credential("LIDARR_API_KEY")
    ld_url = os.environ.get("LIDARR_URL", "").strip()
    if not ld_url:
        try:
            from .web.settings import get_setting
            ld_url = get_setting("LIDARR_URL")
        except Exception:
            pass
    if ld_url:
        cfg["lidarr"]["url"] = ld_url

    return cfg
